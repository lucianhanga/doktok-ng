"""Integration test for chunk storage + hybrid retrieval (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentChunk, DocumentStatus
from doktok_retrieval_hybrid import HybridPostgresRetriever
from doktok_storage_postgres import (
    Database,
    PostgresChunkRepository,
    PostgresDocumentRepository,
)

TENANT = "test-a"
DIM = 1024


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


class FakeEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]


def _document() -> Document:
    return Document(
        id="sdoc",
        tenant_id=TENANT,
        sha256="a" * 64,
        original_filename="sdoc.pdf",
        detected_mime="application/pdf",
        title="Sales doc",
        status=DocumentStatus.ACTIVE,
        storage_path="/docs.active/sdoc",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _chunk(chunk_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        tenant_id=TENANT,
        document_id="sdoc",
        version_id="",
        page_start=1,
        page_end=1,
        heading_path=[],
        text=text,
        token_count=10,
    )


def test_hybrid_search_returns_relevant_chunk(db: Database) -> None:
    PostgresDocumentRepository(db).add(_document())
    PostgresChunkRepository(db).add_chunks(
        [_chunk("c1", "alpha invoice total amount due"), _chunk("c2", "beta contract clause")],
        [_unit_vector(0), _unit_vector(1)],
    )

    # Query vector matches c1's embedding, and the term "invoice" matches c1's text.
    retriever = HybridPostgresRetriever(db, FakeEmbedder(_unit_vector(0)))
    hits = retriever.search(TENANT, "invoice", limit=5)

    assert hits, "expected at least one hit"
    assert hits[0].chunk_id == "c1"
    assert hits[0].original_filename == "sdoc.pdf"
    assert hits[0].title == "Sales doc"
    # Both signals contributed for the top hit.
    assert hits[0].vector_score is not None
    assert hits[0].text_score is not None


def test_semantic_only_query_still_matches(db: Database) -> None:
    PostgresDocumentRepository(db).add(_document())
    PostgresChunkRepository(db).add_chunks(
        [_chunk("c1", "alpha invoice total"), _chunk("c2", "beta contract clause")],
        [_unit_vector(0), _unit_vector(1)],
    )

    # A term with no lexical match -> only the vector signal fires.
    retriever = HybridPostgresRetriever(db, FakeEmbedder(_unit_vector(0)))
    hits = retriever.search(TENANT, "zzqqxx", limit=5)

    assert hits and hits[0].chunk_id == "c1"
    assert hits[0].text_score is None
