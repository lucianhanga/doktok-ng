"""Document tag assignment API (#546): single/bulk assign + list filter + detail aggregate."""

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
    TagRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import AuditEventType, Document, DocumentStatus, Tag, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from doktok_core.tags import InMemoryTagRepository
from fastapi.testclient import TestClient

JWT_SECRET = "t546-tags-secret-32-bytes-min!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _doc(doc_id: str, tenant: str = "tenant-a") -> Document:
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


def _tag(tag_id: str, name: str) -> Tag:
    return Tag(
        id=tag_id,
        tenant_id="tenant-a",
        name=name,
        normalized=name.casefold(),
        created_at=datetime.now(UTC),
    )


def _client(
    tmp_path: Path,
) -> tuple[
    TestClient, InMemoryDocumentRepository, InMemoryTagRepository, InMemoryAuditLogRepository
]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_user(User(id="u_editor", tenant_id="tenant-a", email="e@x.com", role="editor"))
    reg.create_user(User(id="u_viewer", tenant_id="tenant-a", email="v@x.com", role="viewer"))
    docs = InMemoryDocumentRepository()
    for doc_id in ("d1", "d2", "d3"):
        docs.add(_doc(doc_id))
    tags = InMemoryTagRepository()
    tags.create_tag(_tag("t1", "Rome"))
    tags.create_tag(_tag("t2", "Receipts"))
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, docs)  # type: ignore[type-abstract]
    registry.register(TagRepository, tags)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return (
        TestClient(create_app(settings=settings, registry=registry)),
        docs,
        tags,
        audit,
    )


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


EDITOR = _bearer("u_editor")
VIEWER = _bearer("u_viewer")


def test_assign_get_unassign_single_document(tmp_path: Path) -> None:
    client, docs, tags, audit = _client(tmp_path)
    resp = client.put("/api/v1/documents/d1/tags/t1", headers=EDITOR)
    assert resp.status_code == 204, resp.text
    assert [t.name for t in tags.list_for_document("tenant-a", "d1")] == ["Rome"]
    # The document's tags come back on GET + the detail aggregate.
    listed = client.get("/api/v1/documents/d1/tags", headers=VIEWER).json()
    assert [t["name"] for t in listed] == ["Rome"]
    detail = client.get("/api/v1/documents/d1/detail", headers=VIEWER).json()
    assert [t["name"] for t in detail["tags"]] == ["Rome"]
    # Idempotent re-assign; unassign audited both ways.
    assert client.put("/api/v1/documents/d1/tags/t1", headers=EDITOR).status_code == 204
    assert client.delete("/api/v1/documents/d1/tags/t1", headers=EDITOR).status_code == 204
    assert client.get("/api/v1/documents/d1/tags", headers=VIEWER).json() == []
    types = [e.event_type for e in audit.list_events("tenant-a", limit=10)]
    assert types.count(AuditEventType.DOCUMENT_TAGGED.value) == 1
    assert types.count(AuditEventType.DOCUMENT_UNTAGGED.value) == 1


def test_assign_404s_and_role(tmp_path: Path) -> None:
    client, _, _, _ = _client(tmp_path)
    assert client.put("/api/v1/documents/missing/tags/t1", headers=EDITOR).status_code == 404
    assert client.put("/api/v1/documents/d1/tags/missing", headers=EDITOR).status_code == 404
    assert client.put("/api/v1/documents/d1/tags/t1", headers=VIEWER).status_code == 403


def test_bulk_assign_and_remove_with_summary_audit(tmp_path: Path) -> None:
    client, _, tags, audit = _client(tmp_path)
    resp = client.post(
        "/api/v1/documents/tags:bulk",
        json={"document_ids": ["d1", "d2", "d3"], "add": ["t1", "t2"], "remove": []},
        headers=EDITOR,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 3}
    for doc_id in ("d1", "d2", "d3"):
        assert len(tags.list_for_document("tenant-a", doc_id)) == 2
    resp = client.post(
        "/api/v1/documents/tags:bulk",
        json={"document_ids": ["d1", "d2"], "add": [], "remove": ["t2"]},
        headers=EDITOR,
    )
    assert resp.json() == {"updated": 2}
    tagged = [
        e
        for e in audit.list_events("tenant-a", limit=10)
        if e.event_type == AuditEventType.DOCUMENT_TAGGED.value
    ]
    assert len(tagged) == 1  # one summary row for the bulk add, not three
    untagged = [
        e
        for e in audit.list_events("tenant-a", limit=10)
        if e.event_type == AuditEventType.DOCUMENT_UNTAGGED.value
    ]
    assert len(untagged) == 1
    # Bounded input.
    too_many = client.post(
        "/api/v1/documents/tags:bulk",
        json={"document_ids": [f"d{i}" for i in range(501)], "add": ["t1"]},
        headers=EDITOR,
    )
    assert too_many.status_code == 422


def test_list_filters_by_tags_all_and_any(tmp_path: Path) -> None:
    client, docs, tags, _ = _client(tmp_path)
    for doc_id, tag_ids in (("d1", ("t1", "t2")), ("d2", ("t1",)), ("d3", ())):
        for tag_id in tag_ids:
            tags.link("tenant-a", doc_id, tag_id)
            docs.tag_links.setdefault(doc_id, set()).add(tag_id)

    all_match = client.get("/api/v1/documents?tag=t1&tag=t2&tag_match=all", headers=VIEWER).json()
    assert [d["id"] for d in all_match["items"]] == ["d1"]
    any_match = client.get("/api/v1/documents?tag=t1&tag=t2&tag_match=any", headers=VIEWER).json()
    assert sorted(d["id"] for d in any_match["items"]) == ["d1", "d2"]
    none_match = client.get("/api/v1/documents?tag=t2", headers=VIEWER).json()
    assert [d["id"] for d in none_match["items"]] == ["d1"]
    # The ids endpoint (bulk 'select all matching') honors the same filter.
    ids = client.get("/api/v1/documents/ids?tag=t1&tag_match=any", headers=VIEWER).json()
    assert sorted(ids["ids"]) == ["d1", "d2"]


def test_list_response_carries_the_tags_sidecar(tmp_path: Path) -> None:
    client, _, tags, _ = _client(tmp_path)
    tags.link("tenant-a", "d1", "t1")
    tags.link("tenant-a", "d1", "t2")
    page = client.get("/api/v1/documents", headers=VIEWER).json()
    assert [t["name"] for t in page["tags"]["d1"]] == ["Receipts", "Rome"]
    assert page["tags"].get("d2") in (None, [])
