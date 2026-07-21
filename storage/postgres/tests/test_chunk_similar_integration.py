"""Integration tests for similar_documents - semantic neighbors by chunk embedding (#730)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentChunk, DocumentStatus
from doktok_storage_postgres import Database, PostgresChunkRepository, PostgresDocumentRepository

TENANT = "test-a"
DIM = 1024


def _vec(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


def _doc(doc_id: str, tenant: str = TENANT) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        detected_mime="application/pdf",
        title=f"Title {doc_id}",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _chunk(doc_id: str, chunk_id: str, tenant: str = TENANT) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        tenant_id=tenant,
        document_id=doc_id,
        version_id="",
        page_start=1,
        page_end=1,
        heading_path=[],
        text=f"text {chunk_id}",
        token_count=5,
    )


def test_similar_documents_ranked_self_excluded_and_tenant_scoped(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    for doc_id in ("sim-a", "sim-b", "sim-c"):
        docs.add(_doc(doc_id))
    docs.add(_doc("sim-x", tenant="test-b"))
    chunks = PostgresChunkRepository(db)
    # sim-a lives on axis 0; sim-b is identical (sim ~1.0), sim-c orthogonal (sim ~0.0), and
    # sim-x is an identical vector in ANOTHER tenant (must never appear).
    chunks.add_chunks([_chunk("sim-a", "ca")], [_vec(0)])
    chunks.add_chunks([_chunk("sim-b", "cb")], [_vec(0)])
    chunks.add_chunks([_chunk("sim-c", "cc")], [_vec(1)])
    chunks.add_chunks([_chunk("sim-x", "cx", tenant="test-b")], [_vec(0)])

    similar = chunks.similar_documents(TENANT, "sim-a", limit=5)
    by_id = {s.document_id: s for s in similar}
    assert "sim-a" not in by_id  # self excluded
    assert "sim-x" not in by_id  # other tenant never leaks in
    assert by_id["sim-b"].score > 0.99
    assert by_id["sim-b"].score > by_id["sim-c"].score  # identical beats orthogonal
    assert similar[0].document_id == "sim-b"  # ranked by score
    assert similar[0].title == "Title sim-b"
    assert similar[0].original_filename == "sim-b.pdf"


def test_similar_documents_empty_for_a_lone_document(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("sim-lone"))
    PostgresChunkRepository(db).add_chunks([_chunk("sim-lone", "cl")], [_vec(2)])
    assert PostgresChunkRepository(db).similar_documents(TENANT, "sim-lone", limit=5) == []
