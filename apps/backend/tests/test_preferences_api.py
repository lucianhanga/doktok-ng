"""Per-user server-side preferences API (#558): read/write/delete, per-user + local-first scope."""

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import TenantRegistry, UserPreferenceRepository
from doktok_contracts.schemas import Tenant, User
from doktok_core.config import Settings
from doktok_core.preferences.inmemory import InMemoryUserPreferenceRepository
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from fastapi.testclient import TestClient

JWT_SECRET = "prefs-test-secret"  # pragma: allowlist secret
STATIC_TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client() -> TestClient:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u1", tenant_id="tenant-a", email="a@x.com", role="viewer"))
    reg.create_user(User(id="u2", tenant_id="tenant-a", email="b@x.com", role="viewer"))
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(UserPreferenceRepository, InMemoryUserPreferenceRepository())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens=STATIC_TOKENS,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry))


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


def test_empty_by_default() -> None:
    client = _client()
    assert client.get("/api/v1/preferences", headers=_bearer("u1")).json() == {}


def test_put_merges_and_get_returns_all() -> None:
    client = _client()
    u1 = _bearer("u1")
    r1 = client.put("/api/v1/preferences", json={"docLayout": "grid", "thumbSize": 3}, headers=u1)
    assert r1.status_code == 200
    assert r1.json() == {"docLayout": "grid", "thumbSize": 3}
    # A partial PUT merges (thumbSize preserved) and supports nested JSON values.
    r2 = client.put("/api/v1/preferences", json={"chat": {"mode": "agentic"}}, headers=u1)
    assert r2.json() == {"docLayout": "grid", "thumbSize": 3, "chat": {"mode": "agentic"}}
    assert client.get("/api/v1/preferences", headers=u1).json() == r2.json()


def test_delete_removes_one_key() -> None:
    client = _client()
    u1 = _bearer("u1")
    client.put("/api/v1/preferences", json={"a": 1, "b": 2}, headers=u1)
    assert client.delete("/api/v1/preferences/a", headers=u1).status_code == 204
    assert client.get("/api/v1/preferences", headers=u1).json() == {"b": 2}
    # Deleting a missing key is idempotent.
    assert client.delete("/api/v1/preferences/missing", headers=u1).status_code == 204


def test_preferences_are_per_user() -> None:
    client = _client()
    client.put("/api/v1/preferences", json={"docLayout": "grid"}, headers=_bearer("u1"))
    # A different user has their own empty bucket.
    assert client.get("/api/v1/preferences", headers=_bearer("u2")).json() == {}


def test_local_first_tenant_token_has_its_own_bucket() -> None:
    client = _client()
    static = {"Authorization": "Bearer tok-a"}  # tenant-scoped, no user
    client.put("/api/v1/preferences", json={"insightsTab": "graph"}, headers=static)
    # The login-less operator persists prefs (keyed by tenant), distinct from any user bucket.
    assert client.get("/api/v1/preferences", headers=static).json() == {"insightsTab": "graph"}
    assert client.get("/api/v1/preferences", headers=_bearer("u1")).json() == {}


def test_requires_auth() -> None:
    client = _client()
    assert client.get("/api/v1/preferences").status_code == 401
