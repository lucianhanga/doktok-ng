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
