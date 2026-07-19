"""Integration tests for the Postgres record repository + money/currency constraint (test* only)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from doktok_contracts.schemas import (
    AggregationIntent,
    Document,
    DocumentStatus,
    ExtractedRecord,
)
from doktok_storage_postgres import Database, PostgresDocumentRepository, PostgresRecordRepository
from psycopg import errors as pg_errors

TENANT = "test-rec"


def _doc(repo: PostgresDocumentRepository, doc_id: str) -> None:
    repo.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            sha256=(doc_id + "a" * 64)[:64],
            original_filename=f"{doc_id}.pdf",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )


def _rec(doc_id: str, merchant: str, minor: int) -> ExtractedRecord:
    return ExtractedRecord(
        id=uuid.uuid4().hex,
        tenant_id=TENANT,
        document_id=doc_id,
        raw_text=f"{merchant} {minor}",
        occurred_on=date(2026, 1, 5),
        amount_minor=minor,
        currency="EUR",
        direction="debit",
        merchant_raw=merchant,
        merchant_normalized=merchant.lower(),
    )


def test_replace_and_list(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rd1")
    recs.replace_for_document(
        TENANT, "rd1", [_rec("rd1", "Block House", 4500), _rec("rd1", "Amazon", 2350)]
    )
    assert {r.merchant_normalized for r in recs.list_for_document(TENANT, "rd1")} == {
        "block house",
        "amazon",
    }
    # idempotent replace
    recs.replace_for_document(TENANT, "rd1", [_rec("rd1", "Shell", 6000)])
    listed = recs.list_for_document(TENANT, "rd1")
    assert len(listed) == 1 and listed[0].amount_minor == 6000


def test_aggregate_sums_merchant_fuzzy_and_scopes_tenant(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rd3")
    recs.replace_for_document(
        TENANT,
        "rd3",
        [
            _rec("rd3", "BLOCK HOUSE HAMBURG", 4250),
            _rec("rd3", "BLOCKHOUSE #42 MUENCHEN", 3990),  # no space - must still match
            _rec("rd3", "Shell Tankstelle", 7010),
        ],
    )
    result = recs.aggregate(TENANT, AggregationIntent(merchant="block house"))
    assert result.count == 2  # both Block House rows, not Shell
    assert result.by_currency[0].currency == "EUR"
    assert result.by_currency[0].total_minor == 4250 + 3990
    # Tenant isolation: another tenant sees nothing.
    assert recs.aggregate("test-other", AggregationIntent(merchant="block house")).count == 0


def test_aggregate_merchant_escapes_like_wildcards(db: Database) -> None:
    # F-38 (#650): '_' is a LIKE single-char wildcard; a merchant query 'a_b' must match the
    # literal underscore only, not 'axb'.
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rde")
    recs.replace_for_document(
        TENANT,
        "rde",
        [_rec("rde", "a_b store", 1000), _rec("rde", "axb mart", 2000)],
    )
    result = recs.aggregate(TENANT, AggregationIntent(merchant="a_b"))
    assert result.count == 1
    assert result.by_currency[0].total_minor == 1000


def test_amount_requires_currency_constraint(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _doc(docs, "rd2")
    bad = _rec("rd2", "X", 100)
    bad.currency = None  # amount without currency violates the CHECK
    with pytest.raises(pg_errors.CheckViolation):
        PostgresRecordRepository(db).replace_for_document(TENANT, "rd2", [bad])


def test_confidence_defaults_to_null_after_migration(db: Database) -> None:
    # The honest default: a stored record carries no confidence until a model genuinely scores it.
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rdc")
    recs.replace_for_document(TENANT, "rdc", [_rec("rdc", "Aldi", 1999)])
    stored = recs.list_for_document(TENANT, "rdc")
    assert stored[0].confidence is None


def test_record_summary_rollups(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rds")

    def rec(
        rid: str, merchant: str, minor: int, direction: str, currency: str, day: int
    ) -> ExtractedRecord:
        return ExtractedRecord(
            id=rid,
            tenant_id=TENANT,
            document_id="rds",
            raw_text=f"{merchant} {minor}",
            occurred_on=date(2026, 1, day),
            amount_minor=minor,
            currency=currency,
            direction=direction,
            merchant_raw=merchant,
            merchant_normalized=merchant.lower(),
        )

    recs.replace_for_document(
        TENANT,
        "rds",
        [
            rec("s1", "Block House", 4250, "debit", "EUR", 3),
            rec("s2", "Block House", 1000, "credit", "EUR", 9),
            rec("s3", "Shell", 900, "debit", "USD", 1),
        ],
    )
    s = recs.record_summary(TENANT, "rds")
    assert s.total == 3
    by_cur = {c.currency: c for c in s.by_currency}
    assert by_cur["EUR"].debit_minor == 4250
    assert by_cur["EUR"].credit_minor == 1000
    assert by_cur["EUR"].count == 2
    assert by_cur["USD"].debit_minor == 900
    assert s.date_from == date(2026, 1, 1) and s.date_to == date(2026, 1, 9)
    assert s.top_merchants[0].merchant == "block house"  # ranked by count
    assert s.top_merchants[0].count == 2
    # Nothing scores today -> every row is honestly unscored, none low/medium/high.
    assert s.confidence.unscored == 3
    assert s.confidence.high == 0 and s.confidence.low == 0
    assert s.low_confidence_count == 0
    # Tenant isolation: another tenant sees an empty summary.
    assert recs.record_summary("test-other", "rds").total == 0


def test_record_summary_empty_document(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _doc(docs, "rde")
    s = PostgresRecordRepository(db).record_summary(TENANT, "rde")
    assert s.total == 0 and s.by_currency == [] and s.top_merchants == []
    assert s.date_from is None and s.date_to is None


def test_list_for_document_page_orders_and_paginates(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    recs = PostgresRecordRepository(db)
    _doc(docs, "rdp")

    rows = [_rec("rdp", f"m{i}", 100 + i) for i in range(5)]
    for i, r in enumerate(rows):
        r.id = f"p{i}"
        r.occurred_on = date(2026, 1, i + 1)
    # One row with a NULL date must sort last.
    null_row = _rec("rdp", "late", 999)
    null_row.id = "pz"
    null_row.occurred_on = None
    recs.replace_for_document(TENANT, "rdp", [*rows, null_row])

    page1, total = recs.list_for_document_page(TENANT, "rdp", limit=3, offset=0)
    assert total == 6
    assert [r.id for r in page1] == ["p0", "p1", "p2"]  # earliest dates first
    page2, _ = recs.list_for_document_page(TENANT, "rdp", limit=3, offset=3)
    assert [r.id for r in page2] == ["p3", "p4", "pz"]  # null date last
