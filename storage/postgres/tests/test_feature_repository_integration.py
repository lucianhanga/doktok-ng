"""Integration tests for the feature ledger repository (ADR-0009; test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import Document, DocumentStatus, FeatureStatus
from doktok_storage_postgres import Database, PostgresDocumentRepository, PostgresFeatureRepository

TENANT = "test-a"


def _active_doc(repo: PostgresDocumentRepository, doc_id: str) -> None:
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


def test_ensure_creates_rows_only_for_active_documents(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)

    created = repo.ensure_for_active(TENANT, [("chunk_embed", 1), ("entities", 1)])
    assert created == 2
    # idempotent: a second ensure creates nothing new
    assert repo.ensure_for_active(TENANT, [("chunk_embed", 1), ("entities", 1)]) == 0
    assert {r.feature for r in repo.list_for_document(TENANT, "d1")} == {"chunk_embed", "entities"}


def test_claim_mark_retry_and_reset(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)
    repo.ensure_for_active(TENANT, [("chunk_embed", 1), ("entities", 1)])

    now = datetime.now(UTC)
    before = now - timedelta(seconds=900)

    first = repo.claim_next(TENANT, now=now, reclaim_before=before)
    assert first is not None and first.status is FeatureStatus.RUNNING and first.attempts == 1
    repo.mark_done(first.id, feature_version=1)

    second = repo.claim_next(TENANT, now=now, reclaim_before=before)
    assert second is not None and second.feature != first.feature
    repo.mark_failed(second.id, error="boom", next_attempt_at=now + timedelta(hours=1))

    # nothing else due now (one done, one failed with a future backoff)
    assert repo.claim_next(TENANT, now=now, reclaim_before=before) is None

    statuses = {r.feature: r.status for r in repo.list_for_document(TENANT, "d1")}
    assert statuses[first.feature] is FeatureStatus.DONE
    assert statuses[second.feature] is FeatureStatus.FAILED

    # manual retry: reset -> claimable again
    assert repo.reset(TENANT, "d1", second.feature) is True
    assert repo.claim_next(TENANT, now=now, reclaim_before=before) is not None


def test_version_bump_marks_done_rows_stale(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)
    repo.record_done(TENANT, "d1", "chunk_embed", 1)

    # registering version 2 should reset the done row to pending
    repo.ensure_for_active(TENANT, [("chunk_embed", 2)])
    row = next(r for r in repo.list_for_document(TENANT, "d1") if r.feature == "chunk_embed")
    assert row.status is FeatureStatus.PENDING and row.feature_version == 2
