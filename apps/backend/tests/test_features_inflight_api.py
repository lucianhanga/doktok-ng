"""In-flight endpoint: processing documents with their per-stage ledger states (#848)."""

import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(
    doc_repo: InMemoryDocumentRepository, feature_repo: InMemoryFeatureRepository
) -> TestClient:
    registry = build_registry()
    registry.register(FeatureRepository, feature_repo)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _doc(doc_repo: InMemoryDocumentRepository, doc_id: str, status: DocumentStatus) -> None:
    doc_repo.add(
        Document(
            id=doc_id,
            tenant_id="tenant-a",
            sha256=(doc_id + "x" * 64)[:64],
            original_filename=f"{doc_id}.pdf",
            title=None,
            status=status,
            created_at=datetime.now(UTC),
        )
    )


def test_inflight_returns_processing_documents_with_stage_states() -> None:
    docs = InMemoryDocumentRepository()
    features = InMemoryFeatureRepository()
    _doc(docs, "d-proc", DocumentStatus.PROCESSING)
    _doc(docs, "d-active", DocumentStatus.ACTIVE)
    features.seed_for_document("tenant-a", "d-proc", [("extract", 1), ("entities", 1)])
    features.seed_for_document("tenant-a", "d-active", [("extract", 1)])

    body = _client(docs, features).get("/api/v1/features/inflight", headers=AUTH).json()
    assert len(body) == 1  # only the processing document
    entry = body[0]
    assert entry["document_id"] == "d-proc"
    assert entry["filename"] == "d-proc.pdf"
    states = {f["feature"]: f["status"] for f in entry["features"]}
    assert states == {"extract": "pending", "entities": "pending"}


def test_inflight_requires_a_token() -> None:
    resp = _client(InMemoryDocumentRepository(), InMemoryFeatureRepository()).get(
        "/api/v1/features/inflight"
    )
    assert resp.status_code == 401
