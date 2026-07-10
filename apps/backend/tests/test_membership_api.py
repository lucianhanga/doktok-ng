"""Membership lifecycle (#557): user deactivation/reactivation + invitation accept flow."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import Invitation, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from fastapi.testclient import TestClient

JWT_SECRET = "membership-secret"  # pragma: allowlist secret
STATIC_TOKENS = {"tok-admin": "tenant-a"}
_NEW_PW = "brand-new-pw-9"  # pragma: allowlist secret
_PW = "pw-1234567890"  # pragma: allowlist secret (>= 12 chars for the password policy)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client() -> tuple[TestClient, InMemoryTenantRegistry]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u1", tenant_id="tenant-a", email="member@x.com", role="editor"))
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens=STATIC_TOKENS,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry)), reg


def _admin() -> dict[str, str]:
    return {"Authorization": "Bearer tok-admin"}


def _u1_jwt() -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id="u1", secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


# --- deactivation ---


def test_deactivate_blocks_then_reactivate_restores() -> None:
    client, _ = _client()
    # Active user authenticates.
    assert client.get("/api/v1/auth/me", headers=_u1_jwt()).status_code == 200

    deactivated = client.post("/api/v1/admin/users/u1/deactivate", headers=_admin())
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "deactivated"

    # Their still-valid JWT no longer authenticates (immediate revocation).
    assert client.get("/api/v1/auth/me", headers=_u1_jwt()).status_code == 401

    client.post("/api/v1/admin/users/u1/reactivate", headers=_admin())
    assert client.get("/api/v1/auth/me", headers=_u1_jwt()).status_code == 200


def test_cannot_deactivate_self() -> None:
    client, reg = _client()
    reg.create_user(User(id="admin1", tenant_id="tenant-a", email="a@x.com", role="admin"))
    admin_jwt = {
        "Authorization": "Bearer "
        + issue_access_token(
            tenant_id="tenant-a", user_id="admin1", secret=JWT_SECRET, ttl_seconds=3600
        )
    }
    assert (
        client.post("/api/v1/admin/users/admin1/deactivate", headers=admin_jwt).status_code == 400
    )


def test_deactivate_unknown_user_404() -> None:
    client, _ = _client()
    assert client.post("/api/v1/admin/users/nope/deactivate", headers=_admin()).status_code == 404


# --- invitations ---


def test_invite_accept_then_login() -> None:
    client, _ = _client()
    invited = client.post(
        "/api/v1/admin/invitations",
        json={"email": "new@x.com", "role": "editor"},
        headers=_admin(),
    )
    assert invited.status_code == 201, invited.text
    token = invited.json()["token"]

    # Invited user cannot log in yet (no password, status 'invited').
    pre = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "new@x.com", "password": "whatever"},
    )
    assert pre.status_code == 401

    accepted = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": _NEW_PW},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "editor"

    # Now login works.
    login = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "new@x.com", "password": _NEW_PW},
    )
    assert login.status_code == 200


def test_accept_invite_is_single_use() -> None:
    client, _ = _client()
    token = client.post(
        "/api/v1/admin/invitations", json={"email": "once@x.com"}, headers=_admin()
    ).json()["token"]
    first = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": _PW},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": _PW},
    )
    assert second.status_code == 400


def test_accept_invite_bad_token_400() -> None:
    client, _ = _client()
    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": "not-a-real-token", "password": _PW},
    )
    assert resp.status_code == 400


def test_accept_invite_expired_400() -> None:
    client, reg = _client()
    reg.create_user(
        User(id="uexp", tenant_id="tenant-a", email="exp@x.com", role="viewer", status="invited")
    )
    reg.create_invitation(
        Invitation(
            id="inv-exp",
            tenant_id="tenant-a",
            user_id="uexp",
            email="exp@x.com",
            role="viewer",
            token_sha256=hash_token("expired-token"),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": "expired-token", "password": _PW},
    )
    assert resp.status_code == 400


def test_invite_duplicate_email_409() -> None:
    client, _ = _client()
    assert (
        client.post(
            "/api/v1/admin/invitations", json={"email": "member@x.com"}, headers=_admin()
        ).status_code
        == 409
    )


def test_invite_requires_admin() -> None:
    client, _ = _client()
    assert (
        client.post(
            "/api/v1/admin/invitations", json={"email": "x@y.com"}, headers=_u1_jwt()
        ).status_code
        == 403
    )
