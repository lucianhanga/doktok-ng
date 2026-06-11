"""StructuredRecordsFeature: extract + normalize + store records per document. M6.3."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.media import ExtractedTransaction
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.aggregation import InMemoryRecordRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.processors import StructuredRecordsFeature


class FakeFileStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read_bytes(self, path: str) -> bytes:
        return self._content

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeRecordExtractor:
    def __init__(self, rows: list[ExtractedTransaction]) -> None:
        self._rows = rows

    def extract(self, text: str) -> list[ExtractedTransaction]:
        return self._rows


def _doc() -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="amex.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/d1",
        created_at=datetime.now(UTC),
    )


def _run(rows: list[ExtractedTransaction]) -> InMemoryRecordRepository:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    records = InMemoryRecordRepository()
    feature = StructuredRecordsFeature(
        docs, FakeFileStorage(b"statement text"), FakeRecordExtractor(rows), records
    )
    feature.process("t1", "d1")
    return records


def test_stores_normalized_records() -> None:
    rows = [
        ExtractedTransaction("l1", "2026-01-05", "Block House", None, "45.00", "EUR", "debit"),
        ExtractedTransaction("l2", "2026-01-12", "Amazon", None, "23.50", "EUR", "debit"),
        ExtractedTransaction("l3", None, None, None, None, None, None),  # empty -> skipped
    ]
    stored = _run(rows).list_for_document("t1", "d1")
    assert len(stored) == 2
    assert {r.merchant_normalized for r in stored} == {"block house", "amazon"}
    assert sum(r.amount_minor or 0 for r in stored) == 6850


def test_non_financial_document_clears_records() -> None:
    records = _run([])
    assert records.list_for_document("t1", "d1") == []
