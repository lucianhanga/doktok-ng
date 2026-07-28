"""CORS configuration for the mobile app + web clients (#771, APP-10).

The bearer token is the real control; CORS only governs which BROWSER origins may call the API.
The native mobile app needs none (CORS is browser-enforced), but the Expo web dev preview does -
so the dev origins are explicit and the allowlist is env-configurable.
"""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(cors_origins: list[str]) -> TestClient:
    users = InMemoryTenantRegistry()
    users.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    users.create_user(
        User(
            id="u1",
            tenant_id="tenant-a",
            email="alice@example.com",
            display_name="Alice",
            password_hash=hash_password("pw-alice-123"),
        )
    )
    registry = build_registry()
    registry.register(TenantRegistry, users)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret="test-jwt-secret",  # pragma: allowlist secret
        tenant_tokens={},
        cors_origins=cors_origins,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry))


_PREFLIGHT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "Authorization,Content-Type",
}


def test_preflight_from_allowed_origin_gets_cors_headers() -> None:
    client = _client(["http://localhost:8081"])
    resp = client.options(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:8081", **_PREFLIGHT},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8081"


def test_preflight_from_disallowed_origin_gets_no_allow_origin() -> None:
    client = _client(["http://localhost:8081"])
    resp = client.options(
        "/api/v1/auth/login",
        headers={"Origin": "https://evil.example.com", **_PREFLIGHT},
    )
    assert "access-control-allow-origin" not in resp.headers


def test_default_origins_include_the_expo_web_dev_preview() -> None:
    settings = Settings(env="test", auth_jwt_secret="s", tenant_tokens={}, _env_file=None)  # type: ignore[call-arg]
    assert "http://localhost:8081" in settings.cors_origins
