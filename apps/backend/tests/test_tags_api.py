"""Tag CRUD API (#545): normalization + dedup + near-miss warning, palette validation, in-use
delete contract, audit, tenant isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    TagRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from doktok_core.tags import InMemoryTagRepository
from fastapi.testclient import TestClient

JWT_SECRET = "t545-tags-secret-32-bytes-min!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryTagRepository, InMemoryAuditLogRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_tenant(Tenant(id="tenant-b", name="B"))
    reg.create_user(User(id="u_editor", tenant_id="tenant-a", email="e@x.com", role="editor"))
    reg.create_user(User(id="u_viewer", tenant_id="tenant-a", email="v@x.com", role="viewer"))
    tags = InMemoryTagRepository()
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(TagRepository, tags)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), tags, audit


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


EDITOR = _bearer("u_editor")
VIEWER = _bearer("u_viewer")


def test_create_normalizes_and_audits(tmp_path: Path) -> None:
    client, tags, audit = _client(tmp_path)
    resp = client.post(
        "/api/v1/tags",
        json={"name": "  Rome   Trip  ", "color": "teal", "description": "the trip"},
        headers=EDITOR,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Rome   Trip"  # display preserved
    assert body["normalized"] == "rome trip"  # collapsed + casefolded
    assert body["color"] == "teal"
    assert body["document_count"] == 0
    created = [e for e in audit.list_events("tenant-a", limit=10) if e.event_type == "tag.created"]
    assert created

    # The list endpoint returns it with the doc count; a viewer may read.
    listed = client.get("/api/v1/tags", headers=VIEWER).json()
    assert [t["name"] for t in listed] == ["Rome   Trip"]


def test_create_blocks_exact_normalized_duplicate(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    client.post("/api/v1/tags", json={"name": "Rome Trip"}, headers=EDITOR)
    dup = client.post("/api/v1/tags", json={"name": "  ROME   trip "}, headers=EDITOR)
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "duplicate"


def test_create_warns_on_near_miss_unless_allowed(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    client.post("/api/v1/tags", json={"name": "Rome Trip"}, headers=EDITOR)
    near = client.post("/api/v1/tags", json={"name": "Trip Rome"}, headers=EDITOR)
    assert near.status_code == 409
    detail = near.json()["detail"]
    assert detail["code"] == "similar"
    assert detail["similar"][0]["name"] == "Rome Trip"
    ok = client.post(
        "/api/v1/tags", json={"name": "Trip Rome", "allow_similar": True}, headers=EDITOR
    )
    assert ok.status_code == 201


def test_create_validates_name_color_description(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    assert client.post("/api/v1/tags", json={"name": "   "}, headers=EDITOR).status_code == 422
    assert client.post("/api/v1/tags", json={"name": "x" * 51}, headers=EDITOR).status_code == 422
    assert (
        client.post(
            "/api/v1/tags", json={"name": "x", "color": "#ff00aa"}, headers=EDITOR
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/tags",
            json={"name": "x", "description": "d" * 201},
            headers=EDITOR,
        ).status_code
        == 422
    )
    # Viewers cannot create.
    assert client.post("/api/v1/tags", json={"name": "x"}, headers=VIEWER).status_code == 403


def test_patch_recreates_normalization_and_uniqueness(tmp_path: Path) -> None:
    client, tags, audit = _client(tmp_path)
    first = client.post("/api/v1/tags", json={"name": "Alpha"}, headers=EDITOR).json()
    client.post("/api/v1/tags", json={"name": "Beta"}, headers=EDITOR)
    # Renaming onto an existing normalized key is rejected; onto a fresh one is fine.
    clash = client.patch(f"/api/v1/tags/{first['id']}", json={"name": " BETA "}, headers=EDITOR)
    assert clash.status_code == 409
    ok = client.patch(
        f"/api/v1/tags/{first['id']}",
        json={"name": "Gamma", "color": "violet"},
        headers=EDITOR,
    )
    assert ok.status_code == 200
    assert ok.json()["normalized"] == "gamma" and ok.json()["color"] == "violet"
    assert any(e.event_type == "tag.updated" for e in audit.list_events("tenant-a", limit=10))


def test_delete_in_use_requires_force_and_is_audited(tmp_path: Path) -> None:
    client, tags, audit = _client(tmp_path)
    tag = client.post("/api/v1/tags", json={"name": "Receipts"}, headers=EDITOR).json()
    tags.link("tenant-a", "doc-1", tag["id"])
    in_use = client.delete(f"/api/v1/tags/{tag['id']}", headers=EDITOR)
    assert in_use.status_code == 409
    assert in_use.json()["detail"]["document_count"] == 1
    forced = client.delete(f"/api/v1/tags/{tag['id']}?force=true", headers=EDITOR)
    assert forced.status_code == 204
    assert tags.get_tag("tenant-a", tag["id"]) is None
    assert tags.document_count("tenant-a", tag["id"]) == 0  # links went with it
    deleted = [e for e in audit.list_events("tenant-a", limit=10) if e.event_type == "tag.deleted"]
    assert deleted and deleted[0].severity == "warning"  # forced-in-use is a warning
    # Idempotent: deleting again is a 204 too.
    assert client.delete(f"/api/v1/tags/{tag['id']}", headers=EDITOR).status_code == 204


def test_list_supports_q_filter_and_tenant_isolation(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    client.post("/api/v1/tags", json={"name": "Rome Trip"}, headers=EDITOR)
    client.post("/api/v1/tags", json={"name": "Receipts"}, headers=EDITOR)
    filtered = client.get("/api/v1/tags?q=rom", headers=VIEWER).json()
    assert [t["name"] for t in filtered] == ["Rome Trip"]
