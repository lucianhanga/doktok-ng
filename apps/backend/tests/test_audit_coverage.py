"""Audit-coverage gap tests (#635, security audit F-21).

The human uploader's identity was never recorded (the worker later logs actor="worker", so "who
uploaded this file?" was unanswerable), and chat thread/memory deletions left no audit row. Now:
upload records ``document.uploaded`` with the caller's identity, and chat thread delete/truncate +
memory deletion record ``chat.*`` events with the caller as actor.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AuditLogRepository,
    ChatThreadRepository,
    MemoryRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import Memory, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.chat.inmemory import InMemoryChatThreadRepository, InMemoryMemoryRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from fastapi.testclient import TestClient

JWT_SECRET = "f21-test-secret"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


def _client(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryAuditLogRepository, InMemoryChatThreadRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u_edit", tenant_id="tenant-a", email="e@x.com", role="editor"))
    audit = InMemoryAuditLogRepository()
    threads = InMemoryChatThreadRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, threads)  # type: ignore[type-abstract]
    registry.register(MemoryRepository, InMemoryMemoryRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), audit, threads


def test_upload_records_the_uploader_identity(tmp_path: Path) -> None:
    client, audit, _ = _client(tmp_path)
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("invoice.pdf", b"%PDF-1.4 hello", "application/pdf"))],
        headers=_bearer("u_edit"),
    )
    assert resp.status_code == 200
    uploads = [e for e in audit.list_events("tenant-a") if e.event_type == "document.uploaded"]
    assert len(uploads) == 1
    assert uploads[0].actor == "u_edit"
    assert uploads[0].actor_kind == "user"
    assert uploads[0].doc_filename == "invoice.pdf"


def test_chat_thread_delete_and_truncate_are_audited(tmp_path: Path) -> None:
    client, audit, threads = _client(tmp_path)
    thread = threads.create_thread("tenant-a")

    assert (
        client.delete(f"/api/v1/chat/threads/{thread.id}", headers=_bearer("u_edit")).status_code
        == 204
    )
    deleted = [e for e in audit.list_events("tenant-a") if e.event_type == "chat.thread_deleted"]
    assert len(deleted) == 1
    assert deleted[0].actor == "u_edit"
    assert deleted[0].metadata.get("thread_id") == thread.id

    other = threads.create_thread("tenant-a")
    resp = client.delete(
        f"/api/v1/chat/threads/{other.id}/messages/m-1/after", headers=_bearer("u_edit")
    )
    assert resp.status_code == 204
    truncated = [
        e for e in audit.list_events("tenant-a") if e.event_type == "chat.thread_truncated"
    ]
    assert len(truncated) == 1
    assert truncated[0].actor == "u_edit"
    assert truncated[0].metadata.get("thread_id") == other.id
    assert truncated[0].metadata.get("message_id") == "m-1"


def test_memory_deletion_is_audited(tmp_path: Path) -> None:
    client, audit, _ = _client(tmp_path)
    mem = InMemoryMemoryRepository()
    mem.remember("tenant-a", Memory(id="m1", text="Rent is 900 EUR"), [1.0, 0.0])
    # Rewire with the seeded store: build a fresh client carrying it.
    registry = build_registry()
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u_edit", tenant_id="tenant-a", email="e@x.com", role="editor"))
    audit2 = InMemoryAuditLogRepository()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit2)  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, InMemoryChatThreadRepository())  # type: ignore[type-abstract]
    registry.register(MemoryRepository, mem)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    client = TestClient(create_app(settings=settings, registry=registry))

    assert client.delete("/api/v1/chat/memories/m1", headers=_bearer("u_edit")).status_code == 204
    events = [e for e in audit2.list_events("tenant-a") if e.event_type == "chat.memory_deleted"]
    assert len(events) == 1
    assert events[0].actor == "u_edit"
    assert events[0].metadata.get("memory_id") == "m1"

    assert client.delete("/api/v1/chat/memories", headers=_bearer("u_edit")).status_code == 204
    events = [e for e in audit2.list_events("tenant-a") if e.event_type == "chat.memory_deleted"]
    assert len(events) == 2
    # list_events is newest-first; match on metadata instead of position.
    forget_all = [e for e in events if e.metadata.get("all") is True]
    assert len(forget_all) == 1
    assert forget_all[0].actor == "u_edit"
