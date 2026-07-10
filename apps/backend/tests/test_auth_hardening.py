"""Login-endpoint hardening (CISO M2/M5/S2/S3/S4): throttling, password policy, config, audit."""

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.passwords import hash_password
from fastapi.testclient import TestClient

JWT_SECRET = "a" * 40  # >= 32 bytes, no weak-secret warning  # pragma: allowlist secret
GOOD_PW = "correct-horse-battery"  # pragma: allowlist secret (>= 12)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _build(
    *, jwt_secret: str = JWT_SECRET, login_rate: int = 5, ip_rate: int = 20
) -> tuple[TestClient, InMemoryAuditLogRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(
        User(
            id="u1",
            tenant_id="tenant-a",
            email="alice@x.com",
            role="editor",
            password_hash=hash_password(GOOD_PW),
        )
    )
    audit = InMemoryAuditLogRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=jwt_secret,
        tenant_tokens={"tok-a": "tenant-a"},
        login_rate_per_minute=login_rate,
        login_ip_rate_per_minute=ip_rate,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry)), audit


def _client(*, jwt_secret: str = JWT_SECRET, login_rate: int = 5, ip_rate: int = 20) -> TestClient:
    return _build(jwt_secret=jwt_secret, login_rate=login_rate, ip_rate=ip_rate)[0]


def _login(client: TestClient, email: str, password: str) -> int:
    resp = client.post(
        "/api/v1/auth/login",
        json={"tenant_id": "tenant-a", "email": email, "password": password},
    )
    return int(resp.status_code)


def test_auth_config_reports_login_enabled() -> None:
    assert _client().get("/api/v1/auth/config").json() == {"login_enabled": True}
    assert _client(jwt_secret="").get("/api/v1/auth/config").json() == {"login_enabled": False}


def test_login_succeeds_with_good_password() -> None:
    assert _login(_client(), "alice@x.com", GOOD_PW) == 200


def test_login_throttles_by_account() -> None:
    client = _client(login_rate=3, ip_rate=100)  # account bucket is the tighter one
    # Burn the per-account bucket with wrong passwords, then even a correct one is 429.
    codes = [_login(client, "alice@x.com", "wrong-password-xx") for _ in range(3)]
    assert codes == [401, 401, 401]
    assert _login(client, "alice@x.com", GOOD_PW) == 429


def test_login_throttle_is_per_account_not_global() -> None:
    client = _client(login_rate=2, ip_rate=100)
    for _ in range(2):
        _login(client, "alice@x.com", "wrong-password-xx")
    assert _login(client, "alice@x.com", GOOD_PW) == 429  # alice throttled
    # A different account is unaffected (its own bucket) - returns 401, not 429.
    assert _login(client, "someone-else@x.com", "wrong-password-xx") == 401


def test_login_throttles_by_ip() -> None:
    client = _client(login_rate=100, ip_rate=3)  # ip bucket is the tighter one
    seen = {_login(client, f"user{i}@x.com", "wrong-password-xx") for i in range(3)}
    # After 3 attempts from the same IP, the next (any account) is 429.
    assert _login(client, "another@x.com", "wrong-password-xx") == 429
    assert seen == {401}


def test_login_records_audit_events() -> None:
    client, audit = _build()
    assert _login(client, "alice@x.com", "wrong-password-xx") == 401
    assert _login(client, "alice@x.com", GOOD_PW) == 200
    events = audit.list_events("tenant-a")
    types = {e.event_type for e in events}
    assert "auth.login_failed" in types
    assert "auth.login_succeeded" in types
    # The password is never recorded in the audit metadata.
    assert all("password" not in e.metadata for e in events)


def test_password_policy_rejects_short_passwords() -> None:
    client = _client()
    # accept-invite enforces the policy (an unknown token still validates the password first).
    resp = client.post(
        "/api/v1/auth/accept-invite",
        json={"token": "whatever", "password": "short"},  # pragma: allowlist secret
    )
    assert resp.status_code == 422
    assert "at least 12" in resp.json()["detail"]
