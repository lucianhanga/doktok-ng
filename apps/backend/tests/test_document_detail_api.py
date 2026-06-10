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
