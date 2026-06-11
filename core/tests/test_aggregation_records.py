"""Tests for transaction normalization (money -> minor units, merchant, dates). M6.3."""

from __future__ import annotations

from datetime import date

from doktok_contracts.media import ExtractedTransaction
from doktok_core.aggregation import normalize_merchant, normalize_transaction, parse_amount_minor


def test_parse_amount_minor_formats() -> None:
    assert parse_amount_minor("45.00") == 4500
    assert parse_amount_minor("45.00 EUR") == 4500
    assert parse_amount_minor("1,200.50") == 120050  # US thousands
    assert parse_amount_minor("1.200,50") == 120050  # EU thousands
    assert parse_amount_minor("30") == 3000
    assert parse_amount_minor("-12.99") == -1299
    assert parse_amount_minor("") is None
    assert parse_amount_minor(None) is None
    assert parse_amount_minor("n/a") is None


def test_normalize_merchant() -> None:
    assert (
        normalize_merchant("BLOCKHOUSE RESTAURANT #42 HAMBURG")
        == "blockhouse restaurant 42 hamburg"
    )
    assert normalize_merchant("  ") is None


def _txn(**kw: str | None) -> ExtractedTransaction:
    base: dict[str, str | None] = {
        "raw_text": "2026-01-05 BLOCK HOUSE 45.00 EUR",
        "date": "2026-01-05",
        "merchant": "Block House",
        "description": None,
        "amount": "45.00",
        "currency": "EUR",
        "direction": "debit",
    }
    base.update(kw)
    return ExtractedTransaction(**base)  # type: ignore[arg-type]


def test_normalize_transaction_full() -> None:
    rec = normalize_transaction(_txn(), tenant_id="t1", document_id="d1")
    assert rec is not None
    assert rec.amount_minor == 4500 and rec.currency == "EUR"
    assert rec.occurred_on == date(2026, 1, 5)
    assert rec.merchant_normalized == "block house" and rec.direction == "debit"


def test_amount_without_currency_is_dropped() -> None:
    rec = normalize_transaction(_txn(currency=None), tenant_id="t1", document_id="d1")
    assert rec is not None  # kept for the merchant
    assert rec.amount_minor is None and rec.currency is None  # DB requires currency for an amount


def test_empty_row_is_skipped() -> None:
    rec = normalize_transaction(
        _txn(amount=None, currency=None, merchant=None, description=None),
        tenant_id="t1",
        document_id="d1",
    )
    assert rec is None
