"""RBAC enforcement (#556): the method-aware write guard applied per router.

Reads pass for any authenticated caller; content writes require 'editor'; settings writes require
'admin'. A tenant-scoped static token (no user) resolves to 'admin' - local-first backward compat.
"""

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    DocumentRepository,
    IngestionJobRepository,
    KnowledgeGraphRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "rbac-test-secret"  # pragma: allowlist secret
STATIC_TOKENS = {"tok-admin": "tenant-a"}  # tenant-scoped, no user -> admin


def _registry() -> InMemoryTenantRegistry:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    for uid, role in (("u_view", "viewer"), ("u_edit", "editor"), ("u_admin", "admin")):
        reg.create_user(User(id=uid, tenant_id="tenant-a", email=f"{uid}@x.com", role=role))
    return reg


def _client() -> TestClient:
    registry = build_registry()
    registry.register(TenantRegistry, _registry())  # type: ignore[type-abstract]
    registry.register(DocumentRepository, InMemoryDocumentRepository())  # type: ignore[type-abstract]
    registry.register(IngestionJobRepository, InMemoryIngestionJobRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(KnowledgeGraphRepository, InMemoryKnowledgeGraphRepository())  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens=STATIC_TOKENS,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry))


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


# --- reads: allowed for everyone (guard only gates writes) ---


def test_viewer_can_read() -> None:
    client = _client()
    # A GET on a write-guarded router: the read passes the guard (404 for a missing doc, not 403).
    resp = client.get("/api/v1/documents/nope", headers=_bearer("u_view"))
    assert resp.status_code == 404


# --- content writes: require editor ---


def test_viewer_write_is_forbidden() -> None:
    client = _client()
    resp = client.delete("/api/v1/documents/nope", headers=_bearer("u_view"))
    assert resp.status_code == 403
    assert "editor" in resp.json()["detail"]


def test_editor_can_write_content() -> None:
    client = _client()
    resp = client.delete("/api/v1/documents/nope", headers=_bearer("u_edit"))
    assert resp.status_code == 200  # idempotent delete of a missing doc


def test_admin_can_write_content() -> None:
    client = _client()
    resp = client.delete("/api/v1/documents/nope", headers=_bearer("u_admin"))
    assert resp.status_code == 200


# --- settings writes: require admin ---


def test_editor_cannot_write_settings() -> None:
    client = _client()
    resp = client.put("/api/v1/settings/ai", json={}, headers=_bearer("u_edit"))
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]


def test_admin_passes_settings_guard() -> None:
    client = _client()
    # Guard passes for admin; the empty body then fails validation (422) - the point is: not 403.
    resp = client.put("/api/v1/settings/ai", json={}, headers=_bearer("u_admin"))
    assert resp.status_code != 403


# --- local-first: a tenant-scoped static token (no user) is admin ---


def test_static_tenant_token_is_admin() -> None:
    client = _client()
    admin = {"Authorization": "Bearer tok-admin"}
    assert client.delete("/api/v1/documents/nope", headers=admin).status_code == 200
    assert client.put("/api/v1/settings/ai", json={}, headers=admin).status_code != 403


def test_write_requires_auth() -> None:
    client = _client()
    assert client.delete("/api/v1/documents/nope").status_code == 401
