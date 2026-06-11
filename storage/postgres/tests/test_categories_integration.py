"""Integration tests for the Postgres category repository + DB cap triggers (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_storage_postgres import Database, PostgresCategoryRepository, PostgresDocumentRepository
from psycopg import errors as pg_errors

TENANT = "test-cat"


def _doc(repo: PostgresDocumentRepository, doc_id: str) -> None:
    repo.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            sha256="a" * 64,
            original_filename=f"{doc_id}.txt",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )


def test_resolve_link_and_filter(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    cats = PostgresCategoryRepository(db)
    _doc(docs, "dc1")
    _doc(docs, "dc2")

    invoice = cats.create(TENANT, "Invoice", "invoice")
    report = cats.create(TENANT, "Report", "report")
    assert invoice is not None and report is not None

    # dedupe via normalized slug + trigram
    assert cats.find_by_normalized(TENANT, "invoice").id == invoice.id  # type: ignore[union-attr]
    assert cats.find_similar(TENANT, "invoic") is not None  # trigram-close

    cats.set_document_categories(TENANT, "dc1", [invoice.id, report.id])
    cats.set_document_categories(TENANT, "dc2", [invoice.id])

    assert {c.name for c in cats.list_for_document(TENANT, "dc1")} == {"Invoice", "Report"}
    by_invoice = {d.id for d in cats.documents_for_category(TENANT, "Invoice")}
    assert by_invoice == {"dc1", "dc2"}
    summary = {s.name: s.document_count for s in cats.list_summary(TENANT)}
    assert summary == {"Invoice": 2, "Report": 1}


def test_tenant_cap_trigger_blocks_21st_category(db: Database) -> None:
    cats = PostgresCategoryRepository(db)
    for i in range(20):
        assert cats.create(TENANT, f"cat{i:02d}", f"cat{i:02d}") is not None
    # The repo swallows the cap violation and returns None.
    assert cats.create(TENANT, "overflow", "overflow") is None
    assert cats.active_count(TENANT) == 20


def test_document_cap_trigger_blocks_6th_link(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    cats = PostgresCategoryRepository(db)
    _doc(docs, "dc6")
    ids = []
    for i in range(6):
        c = cats.create(TENANT, f"k{i}", f"k{i}")
        assert c is not None
        ids.append(c.id)
    # set_document_categories slices to 5 in code, but the trigger is the hard backstop: a direct
    # 6th insert must be rejected.
    cats.set_document_categories(TENANT, "dc6", ids[:5])
    with pytest.raises(pg_errors.CheckViolation), db.connection() as conn:
        conn.execute(
            "INSERT INTO document_category_links (tenant_id, document_id, category_id) "
            "VALUES (%s, %s, %s)",
            (TENANT, "dc6", ids[5]),
        )
