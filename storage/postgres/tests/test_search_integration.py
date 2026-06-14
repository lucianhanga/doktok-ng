"""Integration test for chunk storage + hybrid retrieval (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from doktok_contracts.schemas import (
    Document,
    DocumentChunk,
    DocumentStatus,
    QueryFilters,
)
from doktok_retrieval_hybrid import HybridPostgresRetriever
from doktok_storage_postgres import (
    Database,
    PostgresCategoryRepository,
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


def _doc(doc_id: str, document_date: date) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        detected_mime="application/pdf",
        title=doc_id,
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        document_date=document_date,
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _doc_chunk(chunk_id: str, doc_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        tenant_id=TENANT,
        document_id=doc_id,
        version_id="",
        page_start=1,
        page_end=1,
        heading_path=[],
        text=text,
        token_count=10,
    )


def test_date_filter_scopes_to_the_range(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    docs.add(_doc("old", date(2023, 6, 1)))
    docs.add(_doc("new", date(2024, 6, 1)))
    PostgresChunkRepository(db).add_chunks(
        [
            _doc_chunk("oc", "old", "invoice total due"),
            _doc_chunk("nc", "new", "invoice total due"),
        ],
        [_unit_vector(0), _unit_vector(0)],
    )
    retriever = HybridPostgresRetriever(db, FakeEmbedder(_unit_vector(0)))

    # No filter: both documents' chunks are eligible.
    assert {h.document_id for h in retriever.search(TENANT, "invoice", limit=5)} == {"old", "new"}
    # 2023 range: only the 2023 document survives.
    filtered = retriever.search(
        TENANT,
        "invoice",
        limit=5,
        filters=QueryFilters(date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)),
    )
    assert {h.document_id for h in filtered} == {"old"}


def test_category_filter_scopes_to_the_category(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    docs.add(_doc("inv", date(2024, 1, 1)))
    docs.add(_doc("ctr", date(2024, 1, 1)))
    PostgresChunkRepository(db).add_chunks(
        [
            _doc_chunk("ic", "inv", "invoice total due"),
            _doc_chunk("cc", "ctr", "invoice total due"),
        ],
        [_unit_vector(0), _unit_vector(0)],
    )
    cats = PostgresCategoryRepository(db)
    invoice = cats.create(TENANT, "Invoice", "invoice")
    assert invoice is not None
    cats.set_document_categories(TENANT, "inv", [invoice.id])
    retriever = HybridPostgresRetriever(db, FakeEmbedder(_unit_vector(0)))

    filtered = retriever.search(
        TENANT, "invoice", limit=5, filters=QueryFilters(category="invoice")
    )
    assert {h.document_id for h in filtered} == {"inv"}


def test_unknown_inferred_category_does_not_exclude_everything(db: Database) -> None:
    # The understand step can infer a category that does not exist in the corpus (e.g. an English
    # label over a German/Romanian corpus). That must be a no-op, not a filter that excludes every
    # document and forces a false refusal.
    docs = PostgresDocumentRepository(db)
    docs.add(_doc("inv", date(2024, 1, 1)))
    docs.add(_doc("ctr", date(2024, 1, 1)))
    PostgresChunkRepository(db).add_chunks(
        [
            _doc_chunk("ic", "inv", "invoice total due"),
            _doc_chunk("cc", "ctr", "invoice total due"),
        ],
        [_unit_vector(0), _unit_vector(0)],
    )
    cats = PostgresCategoryRepository(db)
    invoice = cats.create(TENANT, "Invoice", "invoice")
    assert invoice is not None
    cats.set_document_categories(TENANT, "inv", [invoice.id])
    retriever = HybridPostgresRetriever(db, FakeEmbedder(_unit_vector(0)))

    # "identity card" is not a category in this tenant -> the filter is ignored, both docs returned.
    filtered = retriever.search(
        TENANT, "invoice", limit=5, filters=QueryFilters(category="identity card")
    )
    assert {h.document_id for h in filtered} == {"inv", "ctr"}
