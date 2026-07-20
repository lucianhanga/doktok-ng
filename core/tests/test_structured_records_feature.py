"""StructuredRecordsFeature: extract + normalize + store records per document. M6.3."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.media import ExtractedTransaction, LlmUsage
from doktok_contracts.ports import RecordExtractor
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


class LineRecordExtractor:
    """Returns one transaction per non-blank line ('<merchant> <amount>'), so each window only sees
    its own lines - this is what exercises the windowing + seam dedup, unlike the fixed-rows fake.
    Reports per-call usage so the feature's usage summing is exercised too."""

    model = "fake-line-model"

    def __init__(self) -> None:
        self._last_usage: LlmUsage | None = None

    def extract(self, text: str) -> list[ExtractedTransaction]:
        rows: list[ExtractedTransaction] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            merchant, amount = line.rsplit(" ", 1)
            rows.append(
                ExtractedTransaction(line, "2026-01-01", merchant, None, amount, "EUR", "debit")
            )
        self._last_usage = LlmUsage(prompt_tokens=100, answer_tokens=10)
        return rows

    def get_last_usage(self) -> LlmUsage | None:
        return self._last_usage


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
    fake = FakeRecordExtractor(rows)
    feature = StructuredRecordsFeature(
        docs, FakeFileStorage(b"statement text"), lambda _t: fake, records
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


def _run_extractor(
    content: bytes, extractor: RecordExtractor
) -> tuple[InMemoryRecordRepository, StructuredRecordsFeature]:
    docs = InMemoryDocumentRepository()
    docs.add(_doc())
    records = InMemoryRecordRepository()
    feature = StructuredRecordsFeature(
        docs, FakeFileStorage(content), lambda _t: extractor, records
    )
    feature.process("t1", "d1")
    return records, feature


def test_long_document_keeps_tail_transactions_and_dedups_seams() -> None:
    # 1500 unique lines (~27k chars) forces several windows; the head-slice bug dropped everything
    # past ~16k chars. Each merchant is unique so every kept row is a distinct transaction.
    lines = [f"Merchant{i:04d} {i % 90 + 1}.50" for i in range(1500)]
    content = "\n".join(lines).encode("utf-8")
    records, feature = _run_extractor(content, LineRecordExtractor())
    stored = records.list_for_document("t1", "d1")
    assert len(stored) == 1500  # nothing dropped, no seam double-counting
    assert {r.merchant_normalized for r in stored} == {f"merchant{i:04d}" for i in range(1500)}
    # Usage is summed across windows, not just the last call.
    usage = feature.get_last_usage()
    assert usage is not None and usage.prompt_tokens >= 200  # >1 window => >1 call summed


def test_short_document_is_a_single_call_unchanged() -> None:
    content = b"Aldi 10.00\nRewe 20.00"
    records, feature = _run_extractor(content, LineRecordExtractor())
    stored = records.list_for_document("t1", "d1")
    assert sorted(r.merchant_normalized or "" for r in stored) == ["aldi", "rewe"]
    usage = feature.get_last_usage()
    assert usage is not None and usage.prompt_tokens == 100  # exactly one window/call
