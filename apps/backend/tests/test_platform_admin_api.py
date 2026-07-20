"""The platform grant endpoint is gone (#701, epic #700).

There is no user-level platform admin anymore: the grant/revoke endpoint was removed, and no
platform flag appears in admin user views or ``/auth/me``. The platform tier is a HOST
credential (the static token), not a grantable user identity.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import ApiToken, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from fastapi.testclient import TestClient

STATIC = {"Authorization": "Bearer tok-static"}  # the host credential
ALICE = {"Authorization": "Bearer tok-alice"}  # tenant admin user


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _make_client(tmp_path: Path) -> TestClient:
    mem = InMemoryTenantRegistry()
    mem.create_tenant(Tenant(id="tenant-a", name="A"))
    mem.create_user(User(id="alice", tenant_id="tenant-a", email="alice@a.example", role="admin"))
    for token, uid in (("tok-userless", None), ("tok-alice", "alice")):
        mem.create_api_token(
            ApiToken(id=token, tenant_id="tenant-a", user_id=uid, token_sha256=hash_token(token))
        )
    registry = build_registry()
    registry.register(TenantRegistry, mem)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-static": "tenant-a"},
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_platform_grant_endpoint_is_404_for_every_credential(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    for headers in (STATIC, ALICE, {"Authorization": "Bearer tok-userless"}):
        resp = client.post(
            "/api/v1/admin/users/alice/platform-admin",
            headers=headers,
            json={"platform_admin": True},
        )
        assert resp.status_code == 404, headers


def test_user_listing_has_no_platform_flag(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.get("/api/v1/admin/users", headers=STATIC)
    assert resp.status_code == 200
    assert all("is_platform_admin" not in u for u in resp.json())


def test_auth_me_has_no_platform_flag(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    resp = client.get("/api/v1/auth/me", headers=ALICE)
    assert resp.status_code == 200
    assert "is_platform_admin" not in resp.json()
