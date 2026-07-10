"""Audit actor attribution (#560): a logged-in user's actions record their user id as the actor;
a tenant-scoped static token falls back to the tenant id. Both are actor_kind='user'."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    AuditLogRepository,
    CategoryRepository,
    DocumentRepository,
    EntityRepository,
    FeatureRepository,
    RecordRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import Document, DocumentStatus, Tenant, User
from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.categories.inmemory import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from fastapi.testclient import TestClient

JWT_SECRET = "audit-actor-secret"  # pragma: allowlist secret
STATIC_TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "content.md").write_text("hello", encoding="utf-8")
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(
        Document(
            id="d1",
            tenant_id="tenant-a",
            sha256="a" * 64,
            original_filename="note.txt",
            title="note",
            status=DocumentStatus.ACTIVE,
            storage_path=str(tmp_path),
            created_at=datetime.now(UTC),
        )
    )
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="Tenant A"))
    reg.create_user(User(id="u1", tenant_id="tenant-a", email="a@x.com", role="editor"))

    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, InMemoryEntityRepository())  # type: ignore[type-abstract]
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(RecordRepository, InMemoryRecordRepository())  # type: ignore[type-abstract]
    settings = Settings(
        env="test",
        auth_jwt_secret=JWT_SECRET,
        tenant_tokens=STATIC_TOKENS,
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, registry=registry))


def _viewed_actor(client: TestClient, headers: dict[str, str]) -> str:
    body = client.get("/api/v1/documents/d1/detail", headers=headers).json()
    viewed = [e for e in body["recent_activity"] if e["event_type"] == "document.viewed"]
    assert viewed, body["recent_activity"]
    assert viewed[0]["actor_kind"] == "user"
    return str(viewed[0]["actor"])


def test_logged_in_user_action_attributed_to_user_id(tmp_path: Path) -> None:
    client = _client(tmp_path)
    token = issue_access_token(
        tenant_id="tenant-a", user_id="u1", secret=JWT_SECRET, ttl_seconds=3600
    )
    assert _viewed_actor(client, {"Authorization": f"Bearer {token}"}) == "u1"


def test_tenant_scoped_token_action_attributed_to_tenant(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert _viewed_actor(client, {"Authorization": "Bearer tok-a"}) == "tenant-a"
