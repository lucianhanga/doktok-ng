import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.passwords import hash_password
from fastapi.testclient import TestClient

JWT_SECRET = "test-jwt-secret"
STATIC_TOKENS = {"tok-a": "tenant-a"}  # tenant-scoped, no user


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _registry() -> InMemoryTenantRegistry:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(
        User(
            id="u1",
            tenant_id="tenant-a",
            email="alice@example.com",
            display_name="Alice",
            password_hash=hash_password("pw-alice-123"),
        )
    )
    reg.create_user(
        User(
            id="u2",
            tenant_id="tenant-a",
            email="bob@example.com",
            status="deactivated",
            password_hash=hash_password("pw-bob-123"),
        )
    )
    return reg


def _client(*, jwt_secret: str = JWT_SECRET) -> TestClient:
    registry = build_registry()
    registry.register(TenantRegistry, _registry())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=jwt_secret,
        tenant_tokens=STATIC_TOKENS,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_login_success_returns_token_and_user() -> None:
    client = _client()
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "alice@example.com", "password": "pw-alice-123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]
    assert body["user"] == {
        "id": "u1",
        "tenant_id": "tenant-a",
        "email": "alice@example.com",
        "display_name": "Alice",
    }
    assert "password_hash" not in body["user"]


def test_login_wrong_password_is_generic_401() -> None:
    client = _client()
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "alice@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid email or password"


def test_login_unknown_email_is_generic_401() -> None:
    client = _client()
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "nobody@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid email or password"


def test_login_deactivated_user_is_401() -> None:
    client = _client()
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "bob@example.com", "password": "pw-bob-123"},
    )
    assert resp.status_code == 401


def test_login_503_when_no_secret_configured() -> None:
    client = _client(jwt_secret="")
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "alice@example.com", "password": "pw-alice-123"},
    )
    assert resp.status_code == 503


def test_me_with_session_jwt_returns_identity() -> None:
    client = _client()
    token = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": "alice@example.com", "password": "pw-alice-123"},
    ).json()["access_token"]
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "u1"


def test_me_with_tenant_only_token_is_403() -> None:
    # A static tenant-scoped token carries no user identity -> require_user rejects it.
    client = _client()
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer tok-a"})
    assert resp.status_code == 403


def test_me_requires_auth() -> None:
    client = _client()
    assert client.get("/api/v1/auth/me").status_code == 401
