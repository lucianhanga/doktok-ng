"""Deterministic aggregation over extracted records (M6.3) - the Block House scenario."""

from __future__ import annotations

from datetime import date

from doktok_contracts.schemas import AggregationIntent, ExtractedRecord
from doktok_core.aggregation.inmemory import InMemoryRecordRepository

TENANT = "t1"


def _rec(
    rid: str, merchant: str, amount: int, *, when: str, currency: str = "EUR"
) -> ExtractedRecord:
    return ExtractedRecord(
        id=rid,
        tenant_id=TENANT,
        document_id="doc1",
        record_type="card_transaction",
        raw_text=f"{when} {merchant} {amount}",
        occurred_on=date.fromisoformat(when),
        amount_minor=amount,
        currency=currency,
        direction="debit",
        merchant_raw=merchant,
        merchant_normalized=merchant.lower(),
    )


def _repo() -> InMemoryRecordRepository:
    repo = InMemoryRecordRepository()
    repo.replace_for_document(
        TENANT,
        "doc1",
        [
            _rec("r1", "BLOCK HOUSE RESTAURANT HAMBURG", 4250, when="2024-02-03"),
            _rec("r2", "BLOCKHOUSE #42 MUENCHEN", 3990, when="2024-05-19"),
            _rec("r3", "Shell Tankstelle", 7010, when="2024-03-01"),
            _rec("r4", "block house berlin", 5500, when="2023-12-24"),
        ],
    )
    return repo


def test_sum_for_merchant_fuzzy_match() -> None:
    result = _repo().aggregate(TENANT, AggregationIntent(merchant="block house"))
    assert result.count == 3  # three Block House rows, not the Shell one
    assert len(result.by_currency) == 1
    assert result.by_currency[0].currency == "EUR"
    assert result.by_currency[0].total_minor == 4250 + 3990 + 5500  # 13740 cents = 137.40 EUR
    assert {s.id for s in result.samples} == {"r1", "r2", "r4"}


def test_date_range_narrows_results() -> None:
    intent = AggregationIntent(
        merchant="block house", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
    )
    result = _repo().aggregate(TENANT, intent)
    assert result.count == 2  # excludes the 2023 visit
    assert result.by_currency[0].total_minor == 4250 + 3990


def test_tenant_isolation() -> None:
    result = _repo().aggregate("other-tenant", AggregationIntent(merchant="block house"))
    assert result.count == 0 and result.by_currency == []


def test_currency_buckets_are_separate() -> None:
    repo = InMemoryRecordRepository()
    repo.replace_for_document(
        TENANT,
        "doc1",
        [
            _rec("r1", "block house", 1000, when="2024-01-01", currency="EUR"),
            _rec("r2", "block house", 2000, when="2024-01-02", currency="USD"),
        ],
    )
    result = repo.aggregate(TENANT, AggregationIntent(merchant="block house"))
    totals = {b.currency: b.total_minor for b in result.by_currency}
    assert totals == {"EUR": 1000, "USD": 2000}  # never summed across currencies
