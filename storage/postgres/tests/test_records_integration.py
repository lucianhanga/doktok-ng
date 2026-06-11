"""Integration tests for the Postgres record repository + money/currency constraint (test* only)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from doktok_contracts.schemas import Document, DocumentStatus, ExtractedRecord
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


def test_amount_requires_currency_constraint(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _doc(docs, "rd2")
    bad = _rec("rd2", "X", 100)
    bad.currency = None  # amount without currency violates the CHECK
    with pytest.raises(pg_errors.CheckViolation):
        PostgresRecordRepository(db).replace_for_document(TENANT, "rd2", [bad])
