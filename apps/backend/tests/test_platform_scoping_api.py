"""Endpoint tests for host-token vs user-credential scoping (#614/#615/#619/#620, epic #700).

Deployment-spanning surfaces accept ONLY the static host token (the console credential):
portable restore preview/apply, the DRP drill trigger, model-stack writes (AI + OCR settings),
and tenant provisioning. Session users and user api tokens get 403 on all of them - there is no
user flag anymore. Tenant admins keep tenant-scoped user management (and DRP *status* reads);
the host token may additionally create users in ANY tenant (to seed tenant admins).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import ApiToken, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

STATIC = {"Authorization": "Bearer tok-static"}  # the host credential (console), tenant-a
USERLESS = {"Authorization": "Bearer tok-userless"}  # tenant-b admin, not platform
ADMIN = {"Authorization": "Bearer tok-admin"}  # tenant-b admin user, not platform


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _make_client(tmp_path: Path) -> tuple[TestClient, InMemoryTenantRegistry]:
    mem = InMemoryTenantRegistry()
    mem.create_tenant(Tenant(id="tenant-a", name="A"))
    mem.create_tenant(Tenant(id="tenant-b", name="B"))
    mem.create_user(User(id="admin-b", tenant_id="tenant-b", email="admin@b.example", role="admin"))
    for token, tenant, uid in (
        ("tok-userless", "tenant-b", None),
        ("tok-admin", "tenant-b", "admin-b"),
    ):
        mem.create_api_token(
            ApiToken(id=token, tenant_id=tenant, user_id=uid, token_sha256=hash_token(token))
        )
    registry = build_registry()
    registry.register(TenantRegistry, mem)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-static": "tenant-a"},
        files_root=str(tmp_path / "files"),
        backup_dir=str(tmp_path / "backups"),
        backup_export_dir=str(tmp_path / "exports"),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), mem


# --- F-02 (#614): portable restore is platform-only; status stays readable ---


def test_restore_preview_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert client.post("/api/v1/settings/backup/restore/preview", headers=ADMIN).status_code == 403
    # A platform caller passes the guard and fails validation instead (no file attached).
    assert client.post("/api/v1/settings/backup/restore/preview", headers=STATIC).status_code == 422


def test_restore_apply_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert (
        client.post(
            "/api/v1/settings/backup/restore/x/apply", headers=ADMIN, json={"confirm": True}
        ).status_code
        == 403
    )
    # A platform caller passes the guard; the unknown staged id is the next failure (409).
    assert (
        client.post(
            "/api/v1/settings/backup/restore/x/apply", headers=STATIC, json={"confirm": True}
        ).status_code
        == 409
    )


def test_restore_status_stays_readable_for_tenant_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert client.get("/api/v1/settings/backup/restore/status", headers=ADMIN).status_code == 200


# --- F-03/F-08 (#615/#619): model-stack writes are platform-only ---


def test_put_ai_settings_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    # A valid, fully-local update (no-egress defaults on, so OpenAI purposes would 422).
    body = {
        purpose: {"provider": "ollama", "model": "qwen3.6:27b", "num_ctx": 8192, "reasoning": "low"}
        for purpose in ("pipeline", "ner", "keg", "rag")
    }
    assert client.put("/api/v1/settings/ai", headers=ADMIN, json=body).status_code == 403
    assert client.put("/api/v1/settings/ai", headers=USERLESS, json=body).status_code == 403
    assert client.put("/api/v1/settings/ai", headers=STATIC, json=body).status_code == 200


def test_put_ocr_settings_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert client.put("/api/v1/settings/ocr", headers=ADMIN, json={}).status_code == 403
    assert client.put("/api/v1/settings/ocr", headers=STATIC, json={}).status_code == 200


def test_drp_drill_requires_platform_admin_but_status_stays_readable(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert client.post("/api/v1/settings/drp/drill", headers=ADMIN).status_code == 403
    assert client.get("/api/v1/settings/drp", headers=ADMIN).status_code == 200
    assert client.post("/api/v1/settings/drp/drill", headers=STATIC).status_code == 200


# --- F-09 (#620): tenant provisioning is platform-only ---


def test_list_tenants_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert client.get("/api/v1/admin/tenants", headers=ADMIN).status_code == 403
    assert client.get("/api/v1/admin/tenants", headers=STATIC).status_code == 200


def test_create_tenant_requires_platform_admin(tmp_path: Path) -> None:
    client, _ = _make_client(tmp_path)
    assert (
        client.post("/api/v1/admin/tenants", headers=ADMIN, json={"name": "Nope"}).status_code
        == 403
    )
    assert (
        client.post("/api/v1/admin/tenants", headers=STATIC, json={"name": "New"}).status_code
        == 201
    )


# --- platform admins create tenant admins in any tenant (#620) ---


def test_platform_admin_creates_user_in_another_tenant(tmp_path: Path) -> None:
    client, mem = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users",
        headers=STATIC,
        json={"email": "boss@b.example", "role": "admin", "tenant_id": "tenant-b"},
    )
    assert resp.status_code == 201
    user = mem.get_user_by_email("tenant-b", "boss@b.example")
    assert user is not None and user.role == "admin"
    # A tenant admin's cross-tenant attempt is refused.
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=ADMIN,
            json={"email": "boss2@a.example", "role": "admin", "tenant_id": "tenant-a"},
        ).status_code
        == 403
    )


def test_tenant_admin_cannot_create_user_in_another_tenant(tmp_path: Path) -> None:
    client, mem = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users",
        headers=ADMIN,
        json={"email": "evil@a.example", "role": "admin", "tenant_id": "tenant-a"},
    )
    assert resp.status_code == 403
    assert mem.get_user_by_email("tenant-a", "evil@a.example") is None


def test_tenant_admin_creates_user_in_own_tenant_with_explicit_id(tmp_path: Path) -> None:
    client, mem = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users",
        headers=ADMIN,
        json={"email": "ok@b.example", "role": "editor", "tenant_id": "tenant-b"},
    )
    assert resp.status_code == 201
    assert mem.get_user_by_email("tenant-b", "ok@b.example") is not None
