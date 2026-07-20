"""Host-credential-only platform surfaces (#701, epic #700).

There is no user-level platform admin anymore: platform surfaces (tenant provisioning, DRP
actions, model-stack/OCR writes, portable backup export/restore) accept ONLY the static host
token (via="static"). Session JWTs and user api tokens always get 403; the grant endpoint and
the user flag are gone.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "host-only-secret-32-bytes-minimum!"  # pragma: allowlist secret
STATIC = {"Authorization": "Bearer tok-host"}


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


def _client(tmp_path: Path) -> TestClient:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u_admin", tenant_id="tenant-a", email="a@x.com", role="admin"))
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens={"tok-host": "tenant-a"},
        files_root=str(tmp_path),
        backup_dir=str(tmp_path / "backups"),
        backup_export_dir=str(tmp_path / "exports"),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_session_admin_gets_403_on_platform_surfaces(tmp_path: Path) -> None:
    client = _client(tmp_path)
    auth = _bearer("u_admin")
    # Tenant-admin work stays open; every platform surface is closed to session users.
    assert client.get("/api/v1/admin/users", headers=auth).status_code == 200
    assert client.get("/api/v1/admin/tenants", headers=auth).status_code == 403
    assert client.put("/api/v1/settings/ai", json={}, headers=auth).status_code == 403
    assert client.put("/api/v1/settings/ocr", json={}, headers=auth).status_code == 403
    assert client.post("/api/v1/settings/drp/drill", headers=auth).status_code == 403
    assert client.post("/api/v1/settings/backup/export", headers=auth).status_code == 403
    assert client.post("/api/v1/settings/backup/restore/preview", headers=auth).status_code == 403


def test_static_host_token_keeps_platform_access(tmp_path: Path) -> None:
    # The host credential is the console: scripts use it for platform operations.
    client = _client(tmp_path)
    assert client.get("/api/v1/admin/tenants", headers=STATIC).status_code == 200


def test_platform_grant_endpoint_is_gone(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/u_admin/platform-admin",
        json={"platform_admin": True},
        headers=STATIC,
    )
    assert resp.status_code == 404


def test_auth_me_has_no_platform_flag(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get("/api/v1/auth/me", headers=_bearer("u_admin")).json()
    assert "is_platform_admin" not in body
