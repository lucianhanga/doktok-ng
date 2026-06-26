"""Integration tests for the feature ledger repository (ADR-0009; test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import (
    Document,
    DocumentStatus,
    FeatureMetrics,
    FeatureStatus,
)
from doktok_storage_postgres import Database, PostgresDocumentRepository, PostgresFeatureRepository

TENANT = "test-a"


def _active_doc(repo: PostgresDocumentRepository, doc_id: str) -> None:
    repo.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            sha256=(doc_id + "a" * 64)[:64],
            original_filename=f"{doc_id}.txt",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )


def test_ensure_creates_rows_only_for_active_documents(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)

    # 2 processor features + the self-healed 'extract' marker (the 'text' badge) every active doc
    # gets even though it has no reconciler processor.
    created = repo.ensure_for_active(TENANT, [("chunk_embed", 1), ("entities", 1)])
    assert created == 3
    # idempotent: a second ensure creates nothing new
    assert repo.ensure_for_active(TENANT, [("chunk_embed", 1), ("entities", 1)]) == 0
    assert {r.feature for r in repo.list_for_document(TENANT, "d1")} == {
        "chunk_embed",
        "entities",
        "extract",
    }


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


def test_metrics_round_trip_through_mark_done(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)
    repo.ensure_for_active(TENANT, [("doc_metadata", 1)])

    now = datetime.now(UTC)
    before = now - timedelta(seconds=900)
    claimed = repo.claim_next(TENANT, now=now, reclaim_before=before)
    assert claimed is not None
    metrics = FeatureMetrics(
        duration_ms=1700, prompt_tokens=420, answer_tokens=130, model="qwen3:14b", estimated=True
    )
    repo.mark_done(claimed.id, feature_version=1, metrics=metrics)

    row = next(r for r in repo.list_for_document(TENANT, "d1") if r.feature == "doc_metadata")
    assert row.status is FeatureStatus.DONE
    assert row.metrics.duration_ms == 1700
    assert row.metrics.prompt_tokens == 420
    assert row.metrics.answer_tokens == 130
    assert row.metrics.total_tokens == 550  # validator-derived
    assert row.metrics.model == "qwen3:14b"
    assert row.metrics.estimated is True


def test_mark_done_without_metrics_leaves_empty_default(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)
    repo.ensure_for_active(TENANT, [("doc_metadata", 1)])

    now = datetime.now(UTC)
    claimed = repo.claim_next(TENANT, now=now, reclaim_before=now - timedelta(seconds=900))
    assert claimed is not None
    repo.mark_done(claimed.id, feature_version=1)  # no metrics -> column stays the '{}' default

    row = next(r for r in repo.list_for_document(TENANT, "d1") if r.feature == "doc_metadata")
    assert row.metrics.duration_ms == 0 and row.metrics.total_tokens == 0


def test_feature_counts_for_documents_is_batched(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    _active_doc(docs, "d2")
    repo = PostgresFeatureRepository(db)
    repo.record_done(TENANT, "d1", "chunk_embed", 1)
    repo.record_done(TENANT, "d1", "entities", 1)
    repo.record_done(TENANT, "d2", "chunk_embed", 1)
    # Make one feature on d2 fail.
    now = datetime.now(UTC)
    repo.ensure_for_active(TENANT, [("doc_metadata", 1)])
    claimed = repo.claim_next(TENANT, now=now, reclaim_before=now - timedelta(seconds=900))
    assert claimed is not None
    repo.mark_failed(claimed.id, error="boom", next_attempt_at=now + timedelta(hours=1))

    counts = repo.feature_counts_for_documents(TENANT, ["d1", "d2"])
    # Both docs have done rows; exactly one doc_metadata row (on whichever doc was claimed) failed.
    assert counts["d1"][0] >= 2 and counts["d2"][0] >= 1
    total_failed = counts["d1"][1] + counts["d2"][1]
    assert total_failed == 1
    assert repo.feature_counts_for_documents(TENANT, []) == {}
