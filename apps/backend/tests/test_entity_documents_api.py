"""Documents for a KG entity: paginated + batch-fetched (#628, security audit F-18).

The endpoint used to fetch ALL of an entity's mentions and then load each document with its own
query (1 + 2N queries for a hot entity, no pagination). It is now bounded (limit/offset) and
batch-loads the documents in ONE query.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import DocumentRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import Document, EntityType, KgEntity, KgEntityMention
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TENANT = "tenant-a"
AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class CountingDocs(InMemoryDocumentRepository):
    """Counts per-id get() calls so the test can prove the endpoint never does N+1 lookups."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        self.get_calls += 1
        return super().get(tenant_id, document_id)


def _client(tmp_path: Path, *, mention_docs: int) -> tuple[TestClient, CountingDocs]:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            KgEntity(
                id="e1",
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="alice johnson",
            )
        ]
    )
    for i in range(mention_docs):
        kg.replace_mentions_for_document(
            TENANT,
            f"d{i}",
            [
                KgEntityMention(
                    mention_id=f"m{i}",
                    tenant_id=TENANT,
                    canonical_entity_id="e1",
                    document_id=f"d{i}",
                    entity_type=EntityType.PERSON,
                    normalized_value="alice johnson",
                )
            ],
        )
    docs = CountingDocs()
    from datetime import UTC, datetime

    for i in range(mention_docs):
        docs.add(
            Document(
                id=f"d{i}",
                tenant_id=TENANT,
                sha256="a" * 64,
                original_filename=f"d{i}.pdf",
                created_at=datetime.now(UTC),
            )
        )
    registry = build_registry()
    registry.register(KnowledgeGraphRepository, kg)  # type: ignore[type-abstract]
    registry.register(DocumentRepository, docs)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test", tenant_tokens={"tok-a": TENANT}, files_root=str(tmp_path), _env_file=None
    )
    return TestClient(create_app(settings=settings, registry=registry)), docs


def test_entity_documents_are_paginated(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, mention_docs=5)
    resp = client.get("/api/v1/entities/e1/documents?limit=2", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_entity_documents_default_page_returns_all(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, mention_docs=5)
    resp = client.get("/api/v1/entities/e1/documents", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 5


def test_entity_documents_are_batch_fetched(tmp_path: Path) -> None:
    client, docs = _client(tmp_path, mention_docs=3)
    resp = client.get("/api/v1/entities/e1/documents", headers=AUTH)
    assert resp.status_code == 200
    assert {d["id"] for d in resp.json()} == {"d0", "d1", "d2"}
    assert docs.get_calls == 0  # no N+1: every document came from the single batched fetch


def test_mentions_dedup_documents(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, mention_docs=3)
    resp = client.get("/api/v1/entities/e1/documents", headers=AUTH)
    ids = [d["id"] for d in resp.json()]
    assert len(ids) == len(set(ids))
