"""Unit tests for InMemoryRecordRepository.record_summary / list_for_document_page (records v1).

These exercise the same rollup contract the Postgres repository implements via SQL; the Postgres
integration test (storage/postgres/tests) asserts the SQL path agrees against a real DB.
"""

from __future__ import annotations

from datetime import date

from doktok_contracts.schemas import ExtractedRecord
from doktok_core.aggregation.inmemory import InMemoryRecordRepository

TENANT = "tenant-a"
DOC = "d1"


def _rec(
    rid: str,
    *,
    amount: int | None = None,
    currency: str | None = None,
    direction: str | None = None,
    merchant: str | None = None,
    occurred: date | None = None,
    confidence: float | None = None,
    record_type: str = "card_transaction",
) -> ExtractedRecord:
    return ExtractedRecord(
        id=rid,
        tenant_id=TENANT,
        document_id=DOC,
        record_type=record_type,
        raw_text=rid,
        occurred_on=occurred,
        amount_minor=amount,
        currency=currency,
        direction=direction,
        merchant_normalized=merchant,
        confidence=confidence,
    )


def _repo(records: list[ExtractedRecord]) -> InMemoryRecordRepository:
    repo = InMemoryRecordRepository()
    repo.replace_for_document(TENANT, DOC, records)
    return repo


def test_summary_per_currency_debit_credit_count() -> None:
    repo = _repo(
        [
            _rec("r1", amount=4250, currency="EUR", direction="debit", occurred=date(2024, 2, 3)),
            _rec("r2", amount=1000, currency="EUR", direction="credit", occurred=date(2024, 2, 5)),
            _rec("r3", amount=900, currency="USD", direction="debit", occurred=date(2024, 1, 9)),
        ]
    )
    s = repo.record_summary(TENANT, DOC)
    assert s.total == 3
    by_cur = {c.currency: c for c in s.by_currency}
    assert by_cur["EUR"].debit_minor == 4250
    assert by_cur["EUR"].credit_minor == 1000
    assert by_cur["EUR"].count == 2
    assert by_cur["USD"].debit_minor == 900
    assert by_cur["USD"].count == 1
    # Per-currency only: EUR and USD are never combined into one total.
    assert sum(c.count for c in s.by_currency) == 3


def test_summary_date_range_ignores_null_dates() -> None:
    repo = _repo(
        [
            _rec("r1", occurred=date(2024, 3, 1), merchant="a"),
            _rec("r2", occurred=None, merchant="b"),
            _rec("r3", occurred=date(2024, 1, 15), merchant="c"),
        ]
    )
    s = repo.record_summary(TENANT, DOC)
    assert s.date_from == date(2024, 1, 15)
    assert s.date_to == date(2024, 3, 1)


def test_summary_top_merchants_ranked_by_count() -> None:
    repo = _repo(
        [
            _rec("r1", merchant="block house", amount=100, currency="EUR"),
            _rec("r2", merchant="block house", amount=200, currency="EUR"),
            _rec("r3", merchant="shell", amount=300, currency="EUR"),
            _rec("r4", merchant=None, amount=50, currency="EUR"),  # null merchant excluded
        ]
    )
    s = repo.record_summary(TENANT, DOC)
    assert [m.merchant for m in s.top_merchants] == ["block house", "shell"]
    assert s.top_merchants[0].count == 2
    assert s.top_merchants[0].total_minor == 300


def test_summary_confidence_buckets_including_unscored() -> None:
    repo = _repo(
        [
            _rec("r1", confidence=None),  # unscored
            _rec("r2", confidence=None),  # unscored
            _rec("r3", confidence=0.95),  # high
            _rec("r4", confidence=0.6),  # medium
            _rec("r5", confidence=0.2),  # low
        ]
    )
    s = repo.record_summary(TENANT, DOC)
    assert s.confidence.unscored == 2
    assert s.confidence.high == 1
    assert s.confidence.medium == 1
    assert s.confidence.low == 1
    assert s.low_confidence_count == 1


def test_summary_by_type() -> None:
    repo = _repo(
        [
            _rec("r1", record_type="card_transaction"),
            _rec("r2", record_type="card_transaction"),
            _rec("r3", record_type="invoice_line"),
        ]
    )
    s = repo.record_summary(TENANT, DOC)
    counts = {t.record_type: t.count for t in s.by_type}
    assert counts == {"card_transaction": 2, "invoice_line": 1}


def test_summary_empty_document_is_all_zero() -> None:
    s = InMemoryRecordRepository().record_summary(TENANT, "nope")
    assert s.total == 0
    assert s.by_currency == [] and s.by_type == [] and s.top_merchants == []
    assert s.date_from is None and s.date_to is None
    assert s.confidence.unscored == 0 and s.low_confidence_count == 0


def test_summary_null_currency_record() -> None:
    # A record with no amount/currency (merchant-only) still counts; its currency bucket is None.
    repo = _repo([_rec("r1", merchant="paypal", currency=None, direction=None)])
    s = repo.record_summary(TENANT, DOC)
    assert s.total == 1
    assert s.by_currency[0].currency is None
    assert s.by_currency[0].debit_minor == 0 and s.by_currency[0].credit_minor == 0


def test_pagination_orders_by_date_nulls_last_then_id() -> None:
    repo = _repo(
        [
            _rec("b", occurred=date(2024, 5, 1), merchant="x"),
            _rec("a", occurred=None, merchant="y"),
            _rec("c", occurred=date(2024, 1, 1), merchant="z"),
        ]
    )
    page1, total = repo.list_for_document_page(TENANT, DOC, limit=2, offset=0)
    assert total == 3
    assert [r.id for r in page1] == ["c", "b"]  # earliest dates first
    page2, _ = repo.list_for_document_page(TENANT, DOC, limit=2, offset=2)
    assert [r.id for r in page2] == ["a"]  # null date sorts last


def test_pagination_tenant_isolation() -> None:
    repo = _repo([_rec("r1", merchant="a")])
    items, total = repo.list_for_document_page("tenant-b", DOC, limit=10, offset=0)
    assert items == [] and total == 0
    assert repo.record_summary("tenant-b", DOC).total == 0
