import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import DocumentRepository, EntityRepository
from doktok_contracts.schemas import Document, DocumentEntity, DocumentStatus, EntityType
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(storage_path: str) -> TestClient:
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="note.txt",
        detected_mime="text/plain",
        title="note",
        status=DocumentStatus.ACTIVE,
        storage_path=storage_path,
        created_at=datetime.now(UTC),
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    entity_repo = InMemoryEntityRepository()
    entity_repo.add_entities(
        [
            DocumentEntity(
                id="e1",
                tenant_id="tenant-a",
                document_id="d1",
                version_id="",
                entity_text="a@b.com",
                entity_type=EntityType.EMAIL,
                normalized_value="a@b.com",
                frequency=1,
            )
        ]
    )
    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, entity_repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_content_reads_content_md(tmp_path: Path) -> None:
    (tmp_path / "content.md").write_text("hello body text", encoding="utf-8")
    client = _client(str(tmp_path))
    body = client.get("/api/v1/documents/d1/content", headers=_auth()).json()
    assert body == {"document_id": "d1", "content": "hello body text"}


def test_content_other_tenant_is_404(tmp_path: Path) -> None:
    client = _client(str(tmp_path))
    # No such document for tenant-a id 'missing'
    assert client.get("/api/v1/documents/missing/content", headers=_auth()).status_code == 404


def test_document_entities(tmp_path: Path) -> None:
    client = _client(str(tmp_path))
    rows = client.get("/api/v1/documents/d1/entities", headers=_auth()).json()
    assert [r["normalized_value"] for r in rows] == ["a@b.com"]


def _detail_client(tmp_path: Path) -> TestClient:
    from doktok_contracts.ports import (
        AuditLogRepository,
        CategoryRepository,
        FeatureRepository,
    )
    from doktok_core.audit.inmemory import InMemoryAuditLogRepository
    from doktok_core.categories.inmemory import InMemoryCategoryRepository
    from doktok_core.features.inmemory import InMemoryFeatureRepository

    (tmp_path / "content.md").write_text("x" * 9000, encoding="utf-8")  # longer than the excerpt
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="note.txt",
        title="note",
        status=DocumentStatus.ACTIVE,
        storage_path=str(tmp_path),
        created_at=datetime.now(UTC),
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    entity_repo = InMemoryEntityRepository()
    entity_repo.add_entities(
        [
            DocumentEntity(
                id=f"e{i}",
                tenant_id="tenant-a",
                document_id="d1",
                version_id="",
                entity_text=f"v{i}",
                entity_type=EntityType.EMAIL if i % 2 else EntityType.DATE,
                normalized_value=f"v{i}",
                frequency=i,
            )
            for i in range(1, 6)
        ]
    )
    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, entity_repo)  # type: ignore[type-abstract]
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_detail_aggregate(tmp_path: Path) -> None:
    body = _detail_client(tmp_path).get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert body["document"]["id"] == "d1"
    # Entity summary: total + by-type counts + top-by-frequency (not the full list).
    assert body["entities"]["total"] == 5
    assert {b["entity_type"]: b["count"] for b in body["entities"]["by_type"]} == {
        "EMAIL": 3,
        "DATE": 2,
    }
    assert body["entities"]["top"][0]["frequency"] == 5  # highest first
    # Content is a bounded excerpt + the true length (full text fetched lazily elsewhere).
    assert body["content"]["length"] == 9000
    assert len(body["content"]["excerpt"]) == 4000


def test_detail_other_tenant_is_404(tmp_path: Path) -> None:
    client = _detail_client(tmp_path)
    assert client.get("/api/v1/documents/missing/detail", headers=_auth()).status_code == 404


def test_detail_logs_a_view(tmp_path: Path) -> None:
    client = _detail_client(tmp_path)
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    # The view is logged and shows up in the card's own recent-activity trail.
    viewed = [e for e in body["recent_activity"] if e["event_type"] == "document.viewed"]
    assert viewed and viewed[0]["actor_kind"] == "user"
