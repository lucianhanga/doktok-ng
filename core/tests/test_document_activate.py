"""DocumentRepository.activate: flip a 'processing' document to 'active' (ADR-0015 lifecycle)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from doktok_contracts.errors import DuplicateActiveDocumentError
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository


def _processing(doc_id: str = "d1", sha: str = "a" * 64) -> Document:
    return Document(
        id=doc_id,
        tenant_id="t1",
        sha256=sha,
        original_filename=f"{doc_id}.pdf",
        status=DocumentStatus.PROCESSING,
        created_at=datetime.now(UTC),
    )


def test_activate_flips_processing_to_active() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_processing())
    assert repo.activate(
        "t1",
        "d1",
        storage_path="/docs.active/d1",
        metadata={"extraction_method": "ocr", "page_count": 2},
    )
    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.status is DocumentStatus.ACTIVE
    assert doc.storage_path == "/docs.active/d1"
    assert doc.metadata["extraction_method"] == "ocr"
    assert doc.activated_at is not None and doc.ingested_at is not None


def test_activate_is_noop_when_not_processing() -> None:
    repo = InMemoryDocumentRepository()
    already = _processing()
    already.status = DocumentStatus.ACTIVE
    repo.add(already)
    # Not 'processing' -> returns False, no error (idempotent / lost-the-race safe).
    assert repo.activate("t1", "d1", storage_path="/x", metadata={}) is False


def test_activate_raises_on_duplicate_active_content() -> None:
    repo = InMemoryDocumentRepository()
    winner = _processing("d1")
    winner.status = DocumentStatus.ACTIVE  # already active with sha a*64
    repo.add(winner)
    repo.add(_processing("d2"))  # same content, still processing

    with pytest.raises(DuplicateActiveDocumentError):
        repo.activate("t1", "d2", storage_path="/x", metadata={})
