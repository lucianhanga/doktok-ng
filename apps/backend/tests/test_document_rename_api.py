"""Document rename API (#537): PATCH /documents/{id}/title marks title_source='manual' so the
doc_metadata feature never clobbers it; DELETE hands it back to the auto path. Editor-gated,
tenant-scoped, audited."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    DocumentRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import AuditEventType, Document, DocumentStatus, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "t537-rename-secret-32-bytes-min!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _doc(doc_id: str, tenant: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.txt",
        detected_mime="text/plain",
        title=f"auto-{doc_id}",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _client(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryDocumentRepository, InMemoryAuditLogRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_tenant(Tenant(id="tenant-b", name="B"))
    reg.create_user(User(id="u_editor", tenant_id="tenant-a", email="e@x.com", role="editor"))
    reg.create_user(User(id="u_viewer", tenant_id="tenant-a", email="v@x.com", role="viewer"))
    docs = InMemoryDocumentRepository()
    docs.add(_doc("doc-1", "tenant-a"))
    docs.add(_doc("doc-b", "tenant-b"))
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, docs)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), docs, audit


def _bearer(user_id: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_access_token(
        tenant_id=tenant, user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


EDITOR = _bearer("u_editor")
VIEWER = _bearer("u_viewer")


def test_rename_marks_title_manual_and_audits(tmp_path: Path) -> None:
    client, docs, audit = _client(tmp_path)
    resp = client.patch(
        "/api/v1/documents/doc-1/title", json={"title": "My own name"}, headers=EDITOR
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "My own name"
    assert body["title_source"] == "manual"
    stored = docs.get("tenant-a", "doc-1")
    assert stored is not None and stored.title_source == "manual"
    events = audit.list_events("tenant-a", limit=10)
    renamed = [e for e in events if e.event_type == AuditEventType.DOCUMENT_RENAMED.value]
    assert renamed, "a document.renamed audit row is written"
    assert renamed[0].metadata == {"old_title": "auto-doc-1", "new_title": "My own name"}


def test_rename_rejects_empty_and_whitespace_and_overlong(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert (
        client.patch(
            "/api/v1/documents/doc-1/title", json={"title": "   "}, headers=EDITOR
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/documents/doc-1/title", json={"title": ""}, headers=EDITOR
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/documents/doc-1/title", json={"title": "x" * 201}, headers=EDITOR
        ).status_code
        == 422
    )


def test_rename_requires_editor_role(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert (
        client.patch(
            "/api/v1/documents/doc-1/title", json={"title": "x"}, headers=VIEWER
        ).status_code
        == 403
    )
    assert client.delete("/api/v1/documents/doc-1/title", headers=VIEWER).status_code == 403


def test_rename_is_tenant_scoped(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    # doc-b lives in tenant-b: tenant-a's editor gets a 404, not the document.
    assert (
        client.patch(
            "/api/v1/documents/doc-b/title", json={"title": "x"}, headers=EDITOR
        ).status_code
        == 404
    )


def test_reset_title_hands_it_back_to_auto(tmp_path: Path) -> None:
    client, docs, audit = _client(tmp_path)
    client.patch("/api/v1/documents/doc-1/title", json={"title": "Mine"}, headers=EDITOR)
    resp = client.delete("/api/v1/documents/doc-1/title", headers=EDITOR)
    assert resp.status_code == 200, resp.text
    assert resp.json()["title_source"] == "auto"
    stored = docs.get("tenant-a", "doc-1")
    # The title text itself stays until the next metadata run re-derives it.
    assert stored is not None and stored.title == "Mine" and stored.title_source == "auto"
    assert (
        len(
            [
                e
                for e in audit.list_events("tenant-a", limit=10)
                if e.event_type == "document.renamed"
            ]
        )
        == 2
    )
