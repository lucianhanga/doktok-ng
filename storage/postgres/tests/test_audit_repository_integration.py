"""Integration tests for the Postgres audit log repository (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import AuditEvent
from doktok_storage_postgres import Database, PostgresAuditLogRepository

TEST_TENANT_A = "test-a"
TEST_TENANT_B = "test-b"


def _event(event_id: str, tenant: str, event_type: str, document_id: str | None) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        tenant_id=tenant,
        event_type=event_type,
        actor="worker",
        document_id=document_id,
        timestamp=datetime.now(UTC),
        metadata={"summary": "did a thing"},
    )


def test_record_list_scope_and_filter(db: Database) -> None:
    repo = PostgresAuditLogRepository(db)
    repo.record(_event("ev1", TEST_TENANT_A, "document.received", "doc-a"))
    repo.record(_event("ev2", TEST_TENANT_A, "document.activated", "doc-a"))
    repo.record(_event("ev3", TEST_TENANT_B, "document.received", "doc-b"))

    a_events = repo.list_events(TEST_TENANT_A)
    assert {e.id for e in a_events} == {"ev1", "ev2"}
    assert a_events[0].metadata == {"summary": "did a thing"}

    doc_a = repo.list_events(TEST_TENANT_A, document_id="doc-a")
    assert {e.id for e in doc_a} == {"ev1", "ev2"}
    assert repo.list_events(TEST_TENANT_A, document_id="doc-b") == []


def test_enhanced_activity_fields_round_trip(db: Database) -> None:
    repo = PostgresAuditLogRepository(db)
    event = AuditEvent(
        id="ev-rich",
        tenant_id=TEST_TENANT_A,
        event_type="feature.classified",
        actor="reconciler",
        actor_kind="worker",
        document_id="doc-a",
        timestamp=datetime.now(UTC),
        metadata={"before": "x", "after": "y"},
        severity="warning",
        phase="enrich",
        description="Reclassified document category",
        record_kind="category",
        record_id="cat-7",
        doc_filename="invoice.pdf",
        doc_title="Invoice 2026",
    )
    repo.record(event)

    (stored,) = repo.list_events(TEST_TENANT_A, document_id="doc-a")
    assert stored.severity == "warning"
    assert stored.phase == "enrich"
    assert stored.description == "Reclassified document category"
    assert stored.record_kind == "category"
    assert stored.record_id == "cat-7"
    # No documents row exists for doc-a, so the supplied snapshot is kept verbatim.
    assert stored.doc_filename == "invoice.pdf"
    assert stored.doc_title == "Invoice 2026"
    assert stored.metadata == {"before": "x", "after": "y"}
