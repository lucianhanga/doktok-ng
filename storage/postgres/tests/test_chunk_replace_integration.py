"""Transactional chunk-index replacement for large-document embedding retries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from doktok_contracts.schemas import Document, DocumentChunk, DocumentStatus
from doktok_storage_postgres import Database, PostgresChunkRepository, PostgresDocumentRepository

TENANT = "test-a"
DIM = 1024


def _vec(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        detected_mime="application/pdf",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _chunk(doc_id: str, chunk_id: str) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        tenant_id=TENANT,
        document_id=doc_id,
        version_id="",
        page_start=1,
        page_end=1,
        heading_path=[],
        text=f"text {chunk_id}",
        token_count=5,
    )


def test_replace_swaps_only_the_target_documents_index(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    docs.add(_doc("replace-a"))
    docs.add(_doc("replace-b"))
    chunks = PostgresChunkRepository(db)
    chunks.add_chunks(
        [_chunk("replace-a", "old-a"), _chunk("replace-b", "keep-b")],
        [_vec(0), _vec(1)],
    )

    assert hasattr(chunks, "replace_for_document")
    chunks.replace_for_document(
        TENANT,
        "replace-a",
        [_chunk("replace-a", "new-a-1"), _chunk("replace-a", "new-a-2")],
        [_vec(2), _vec(3)],
    )

    rows = chunks.read_embeddings(TENANT, 10)
    by_id = {chunk_id: (document_id, embedding) for chunk_id, document_id, embedding in rows}
    assert "old-a" not in by_id
    assert by_id["new-a-1"] == ("replace-a", _vec(2))
    assert by_id["new-a-2"] == ("replace-a", _vec(3))
    assert by_id["keep-b"] == ("replace-b", _vec(1))


def test_replace_rolls_back_when_embeddings_are_missing(db: Database) -> None:
    PostgresDocumentRepository(db).add(_doc("replace-rollback"))
    chunks = PostgresChunkRepository(db)
    chunks.add_chunks([_chunk("replace-rollback", "rollback-old")], [_vec(0)])

    assert hasattr(chunks, "replace_for_document")
    with pytest.raises(ValueError):
        chunks.replace_for_document(
            TENANT,
            "replace-rollback",
            [_chunk("replace-rollback", "rollback-new")],
            [],
        )

    rows = chunks.read_embeddings(TENANT, 10)
    assert [(chunk_id, document_id) for chunk_id, document_id, _ in rows] == [
        ("rollback-old", "replace-rollback")
    ]
