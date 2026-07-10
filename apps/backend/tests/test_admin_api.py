"""Tenant/member administration API (#559): admin-only CRUD for tenants, users, roles, tokens."""

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from fastapi.testclient import TestClient

JWT_SECRET = "admin-api-secret"  # pragma: allowlist secret
STATIC_TOKENS = {"tok-admin": "tenant-a"}  # tenant-scoped -> admin (local-first)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _registry() -> InMemoryTenantRegistry:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u_admin", tenant_id="tenant-a", email="admin@x.com", role="admin"))
    reg.create_user(User(id="u_view", tenant_id="tenant-a", email="viewer@x.com", role="viewer"))
    return reg


def _client() -> tuple[TestClient, InMemoryTenantRegistry]:
    reg = _registry()
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
    return {"Authorization": "Bearer tok-admin"}  # static token resolves to admin


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


# --- authorization ---


def test_non_admin_cannot_read_or_write_admin_api() -> None:
    client, _ = _client()
    viewer = _bearer("u_view")
    assert client.get("/api/v1/admin/users", headers=viewer).status_code == 403
    assert (
        client.post("/api/v1/admin/users", json={"email": "x@y.com"}, headers=viewer).status_code
        == 403
    )


def test_admin_api_requires_auth() -> None:
    client, _ = _client()
    assert client.get("/api/v1/admin/users").status_code == 401


# --- users / roles ---


def test_create_list_and_change_user_role() -> None:
    client, reg = _client()
    created = client.post(
        "/api/v1/admin/users",
        json={"email": "New@Example.com", "display_name": "New", "role": "editor"},
        headers=_admin(),
    )
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert created.json()["role"] == "editor"
    assert "password_hash" not in created.json()

    emails = {u["email"] for u in client.get("/api/v1/admin/users", headers=_admin()).json()}
    assert "New@Example.com" in emails

    changed = client.post(
        f"/api/v1/admin/users/{new_id}/role", json={"role": "admin"}, headers=_admin()
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "admin"
    assert reg.get_user("tenant-a", new_id).role == "admin"  # type: ignore[union-attr]


def test_create_user_rejects_duplicate_email() -> None:
    client, _ = _client()
    assert (
        client.post(
            "/api/v1/admin/users", json={"email": "admin@x.com"}, headers=_admin()
        ).status_code
        == 409
    )


def test_invalid_role_is_422() -> None:
    client, _ = _client()
    resp = client.post(
        "/api/v1/admin/users", json={"email": "z@y.com", "role": "superuser"}, headers=_admin()
    )
    assert resp.status_code == 422


def test_set_password_then_login() -> None:
    client, _ = _client()
    new_id = client.post(
        "/api/v1/admin/users", json={"email": "pw@x.com", "role": "editor"}, headers=_admin()
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/admin/users/{new_id}/password",
            json={"password": "s3cret-pw-123"},  # pragma: allowlist secret
            headers=_admin(),
        ).status_code
        == 204
    )
    # The user can now log in with the admin-set password.
    login = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "pw@x.com", "password": "s3cret-pw-123"},
    )
    assert login.status_code == 200, login.text


# --- API tokens ---


def test_issue_token_returns_plaintext_once_and_resolves() -> None:
    client, reg = _client()
    issued = client.post(
        "/api/v1/admin/tokens", json={"name": "ci", "user_id": "u_admin"}, headers=_admin()
    )
    assert issued.status_code == 201, issued.text
    plaintext = issued.json()["token"]
    token_id = issued.json()["id"]
    assert plaintext and issued.json()["token_prefix"] == plaintext[:8]

    # The issued token authenticates as its bound user.
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {plaintext}"})
    assert me.status_code == 200 and me.json()["id"] == "u_admin"

    # Listing exposes only the prefix, never the secret/hash.
    listed = client.get("/api/v1/admin/tokens", headers=_admin()).json()
    row = next(t for t in listed if t["id"] == token_id)
    assert row["active"] is True
    assert "token" not in row and "token_sha256" not in row

    # Revocation stops it resolving.
    assert client.delete(f"/api/v1/admin/tokens/{token_id}", headers=_admin()).status_code == 204
    assert reg.resolve_token(hash_token(plaintext)) is None


def test_issue_token_for_unknown_user_is_404() -> None:
    client, _ = _client()
    assert (
        client.post("/api/v1/admin/tokens", json={"user_id": "nope"}, headers=_admin()).status_code
        == 404
    )


# --- tenants ---


def test_create_tenant_generates_a_guid_id() -> None:
    import uuid

    client, _ = _client()
    created = client.post("/api/v1/admin/tenants", json={"name": "Second"}, headers=_admin())
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert created.json()["name"] == "Second"
    # The id is a server-generated UUID, not client-supplied.
    assert uuid.UUID(new_id)  # raises if not a valid UUID

    tenants = client.get("/api/v1/admin/tenants", headers=_admin()).json()
    ids = {t["id"] for t in tenants}
    assert {"tenant-a", new_id} <= ids


def test_create_tenant_ignores_client_supplied_id() -> None:
    import uuid

    client, _ = _client()
    # A client that tries to set the id is ignored (extra field); the server still generates one.
    created = client.post(
        "/api/v1/admin/tenants", json={"id": "hacked", "name": "Third"}, headers=_admin()
    )
    assert created.status_code == 201
    assert created.json()["id"] != "hacked"
    assert uuid.UUID(created.json()["id"])
