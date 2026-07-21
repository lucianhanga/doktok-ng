"""Document notes API (#736): add/list notes; author-or-admin deletion; both sides audited."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    DocumentNoteRepository,
    DocumentRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import AuditEventType, Document, DocumentStatus, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import (
    InMemoryDocumentNoteRepository,
    InMemoryDocumentRepository,
)
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "t736-notes-secret-32-bytes-min!"  # pragma: allowlist secret


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
        title=doc_id,
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _client(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryDocumentNoteRepository, InMemoryAuditLogRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_tenant(Tenant(id="tenant-b", name="B"))
    reg.create_user(User(id="u_editor", tenant_id="tenant-a", email="e@x.com", role="editor"))
    reg.create_user(User(id="u_editor2", tenant_id="tenant-a", email="e2@x.com", role="editor"))
    reg.create_user(User(id="u_admin", tenant_id="tenant-a", email="a@x.com", role="admin"))
    reg.create_user(User(id="u_viewer", tenant_id="tenant-a", email="v@x.com", role="viewer"))
    docs = InMemoryDocumentRepository()
    docs.add(_doc("doc-1", "tenant-a"))
    docs.add(_doc("doc-b", "tenant-b"))
    notes = InMemoryDocumentNoteRepository()
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, docs)  # type: ignore[type-abstract]
    registry.register(DocumentNoteRepository, notes)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), notes, audit


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


EDITOR = _bearer("u_editor")
EDITOR2 = _bearer("u_editor2")
ADMIN = _bearer("u_admin")
VIEWER = _bearer("u_viewer")


def test_add_list_newest_first_and_audited(tmp_path: Path) -> None:
    client, _, audit = _client(tmp_path)
    first = client.post("/api/v1/documents/doc-1/notes", json={"body": "first"}, headers=EDITOR)
    assert first.status_code == 201, first.text
    assert first.json()["author_email"] == "e@x.com"
    client.post("/api/v1/documents/doc-1/notes", json={"body": "second"}, headers=EDITOR2)

    listed = client.get("/api/v1/documents/doc-1/notes", headers=VIEWER)
    assert listed.status_code == 200  # any tenant reader may read
    assert [n["body"] for n in listed.json()] == ["second", "first"]  # newest first
    assert listed.json()[1]["created_at"]
    events = audit.list_events("tenant-a", limit=10)
    added = [e for e in events if e.event_type == AuditEventType.DOCUMENT_NOTE_ADDED.value]
    assert len(added) == 2


def test_add_validates_body_and_role_and_tenant(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert (
        client.post(
            "/api/v1/documents/doc-1/notes", json={"body": "   "}, headers=EDITOR
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/documents/doc-1/notes", json={"body": "x" * 2001}, headers=EDITOR
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/documents/doc-1/notes", json={"body": "x"}, headers=VIEWER).status_code
        == 403
    )
    # doc-b lives in tenant-b: tenant-a users get a 404, not the document.
    assert (
        client.post("/api/v1/documents/doc-b/notes", json={"body": "x"}, headers=EDITOR).status_code
        == 404
    )
    assert client.get("/api/v1/documents/doc-b/notes", headers=EDITOR).status_code == 404


def _add_note(client: TestClient, body: str, headers: dict[str, str]) -> str:
    resp = client.post("/api/v1/documents/doc-1/notes", json={"body": body}, headers=headers)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_delete_author_ok_other_editor_forbidden_admin_ok(tmp_path: Path) -> None:
    client, _, audit = _client(tmp_path)
    own = _add_note(client, "mine", EDITOR)
    foreign = _add_note(client, "theirs", EDITOR2)

    # The other editor cannot delete someone else's note.
    assert (
        client.delete(f"/api/v1/documents/doc-1/notes/{foreign}", headers=EDITOR).status_code == 403
    )
    # The author can.
    assert client.delete(f"/api/v1/documents/doc-1/notes/{own}", headers=EDITOR).status_code == 204
    # And an admin can delete anyone's.
    assert (
        client.delete(f"/api/v1/documents/doc-1/notes/{foreign}", headers=ADMIN).status_code == 204
    )
    deleted = [
        e
        for e in audit.list_events("tenant-a", limit=10)
        if e.event_type == AuditEventType.DOCUMENT_NOTE_DELETED.value
    ]
    assert len(deleted) == 2
    # The audit keeps a body snapshot of the deleted note.
    assert deleted[0].metadata.get("body") == "theirs"


def test_delete_missing_note_is_404(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.delete("/api/v1/documents/doc-1/notes/nope", headers=ADMIN).status_code == 404
