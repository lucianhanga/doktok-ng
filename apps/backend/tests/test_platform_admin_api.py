"""Endpoint tests for platform-admin grant/revoke (#613, ADR-0025).

Only existing platform admins may grant or revoke the ``is_platform_admin`` flag (never through
user creation and never self-bootstrap); self-revoke is blocked so an operator cannot lock the
deployment out of its platform surfaces. The flag is visible in admin user views and ``/auth/me``
(so the SPA can reflect platform status), and every change is audited.
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

STATIC = {"Authorization": "Bearer tok-static"}  # platform admin (host token)
USERLESS = {"Authorization": "Bearer tok-userless"}  # tenant admin, not platform
ALICE = {"Authorization": "Bearer tok-alice"}  # tenant admin user, not platform
PADMIN = {"Authorization": "Bearer tok-padmin"}  # platform admin user


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _make_client(
    tmp_path: Path,
) -> tuple[TestClient, InMemoryTenantRegistry, InMemoryAuditLogRepository]:
    mem = InMemoryTenantRegistry()
    mem.create_tenant(Tenant(id="tenant-a", name="A"))
    mem.create_user(User(id="alice", tenant_id="tenant-a", email="alice@a.example", role="admin"))
    mem.create_user(
        User(
            id="padmin",
            tenant_id="tenant-a",
            email="padmin@a.example",
            role="admin",
            is_platform_admin=True,
        )
    )
    for token, uid in (("tok-userless", None), ("tok-alice", "alice"), ("tok-padmin", "padmin")):
        mem.create_api_token(
            ApiToken(id=token, tenant_id="tenant-a", user_id=uid, token_sha256=hash_token(token))
        )
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, mem)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-static": "tenant-a"},
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), mem, audit


def test_platform_admin_grants_the_flag(tmp_path: Path) -> None:
    client, mem, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/alice/platform-admin", headers=STATIC, json={"platform_admin": True}
    )
    assert resp.status_code == 200
    assert resp.json()["is_platform_admin"] is True
    assert mem.get_user("tenant-a", "alice").is_platform_admin is True  # type: ignore[union-attr]


def test_platform_admin_revokes_the_flag(tmp_path: Path) -> None:
    client, mem, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/padmin/platform-admin", headers=STATIC, json={"platform_admin": False}
    )
    assert resp.status_code == 200
    assert resp.json()["is_platform_admin"] is False
    assert mem.get_user("tenant-a", "padmin").is_platform_admin is False  # type: ignore[union-attr]


def test_userless_tenant_token_cannot_grant(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/alice/platform-admin", headers=USERLESS, json={"platform_admin": True}
    )
    assert resp.status_code == 403


def test_non_platform_admin_user_cannot_grant(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/alice/platform-admin", headers=ALICE, json={"platform_admin": True}
    )
    assert resp.status_code == 403


def test_platform_user_admin_can_grant(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/alice/platform-admin", headers=PADMIN, json={"platform_admin": True}
    )
    assert resp.status_code == 200


def test_self_revoke_is_blocked(tmp_path: Path) -> None:
    client, mem, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/padmin/platform-admin",
        headers=PADMIN,
        json={"platform_admin": False},
    )
    assert resp.status_code == 400
    assert mem.get_user("tenant-a", "padmin").is_platform_admin is True  # type: ignore[union-attr]


def test_grant_on_unknown_user_is_404(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.post(
        "/api/v1/admin/users/missing/platform-admin", headers=STATIC, json={"platform_admin": True}
    )
    assert resp.status_code == 404


def test_grant_is_audited(tmp_path: Path) -> None:
    client, _, audit = _make_client(tmp_path)
    client.post(
        "/api/v1/admin/users/alice/platform-admin", headers=STATIC, json={"platform_admin": True}
    )
    events = audit.list_events("tenant-a", limit=50)
    matches = [e for e in events if e.event_type == "user.platform_admin_changed"]
    assert len(matches) == 1
    assert matches[0].record_id == "alice"
    assert matches[0].actor == "tenant-a"  # static token: actor is the tenant identity


def test_user_listing_exposes_the_flag(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.get("/api/v1/admin/users", headers=STATIC)
    assert resp.status_code == 200
    flags = {u["id"]: u["is_platform_admin"] for u in resp.json()}
    assert flags == {"alice": False, "padmin": True}


def test_auth_me_exposes_the_flag(tmp_path: Path) -> None:
    client, _, _ = _make_client(tmp_path)
    resp = client.get("/api/v1/auth/me", headers=PADMIN)
    assert resp.status_code == 200
    assert resp.json()["is_platform_admin"] is True
