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
        duration_ms=1700,
        prompt_tokens=420,
        answer_tokens=130,
        model="qwen3.6:27b",
        estimated=True,
    )
    repo.mark_done(claimed.id, feature_version=1, metrics=metrics)

    row = next(r for r in repo.list_for_document(TENANT, "d1") if r.feature == "doc_metadata")
    assert row.status is FeatureStatus.DONE
    assert row.metrics.duration_ms == 1700
    assert row.metrics.prompt_tokens == 420
    assert row.metrics.answer_tokens == 130
    assert row.metrics.total_tokens == 550  # validator-derived
    assert row.metrics.model == "qwen3.6:27b"
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


def test_crashed_running_feature_is_failed_after_max_attempts(db: Database) -> None:
    """A claimed feature whose worker process dies (SIGKILL/OOM) never reaches mark_failed - the
    row stays 'running' and the stale-claim reclaims it forever (2026-10-25 prod incident: 585
    attempts on gliner-relex relations). Crashed attempts must count like failed ones: once
    attempts hit max_attempts the row is failed (surfacing as 'Needs attention') and never
    claimed again."""
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    repo = PostgresFeatureRepository(db)
    repo.ensure_for_active(TENANT, [("relations", 1)])

    t0 = datetime.now(UTC)
    # max_attempts defaults to 3: three crashed attempts (claimed, never completed, reclaimed
    # after the 15-minute stale window).
    for i in range(1, 4):
        now = t0 + timedelta(minutes=16 * i)
        claimed = repo.claim_next(TENANT, now=now, reclaim_before=now - timedelta(minutes=15))
        assert claimed is not None and claimed.attempts == i
        # no mark_done / mark_failed: the worker process died mid-run

    later = t0 + timedelta(minutes=16 * 4)
    assert (
        repo.claim_next(TENANT, now=later, reclaim_before=later - timedelta(minutes=15)) is None
    )
    row = next(r for r in repo.list_for_document(TENANT, "d1") if r.feature == "relations")
    assert row.status is FeatureStatus.FAILED
    assert row.last_error  # explains the crash loop instead of sitting silent


def test_prerequisite_stage_is_claimed_before_the_older_backlog(db: Database) -> None:
    # A fresh document's `extract` row is the NEWEST ledger row; strict FIFO (created_at) parked
    # it behind every older pending feature of already-extracted documents, so OCR sat idle while
    # the reconciler churned the enrichment backlog (user report 2026-10-25). Rows whose feature
    # is a prerequisite of other rows must claim first - they unblock everything downstream.
    docs = PostgresDocumentRepository(db)
    _active_doc(docs, "d1")
    _active_doc(docs, "d2")
    repo = PostgresFeatureRepository(db)

    # d1 seeded EARLIER: its extract is done, its entities row (old) is due and unblocked.
    repo.seed_for_document(TENANT, "d1", [("extract", 1), ("entities", 1)])
    now = datetime.now(UTC)
    first = repo.claim_next(TENANT, now=now, reclaim_before=now - timedelta(seconds=900))
    assert first is not None and first.feature == "extract" and first.document_id == "d1"
    repo.mark_done(first.id, feature_version=1)

    # d2 seeded LATER: extract pending, entities gated behind it.
    repo.seed_for_document(TENANT, "d2", [("extract", 1), ("entities", 1)])

    nxt = repo.claim_next(
        TENANT,
        now=now,
        reclaim_before=now - timedelta(seconds=900),
        dependencies=[("entities", "extract")],
    )
    # Without priority this returns d1's (older) entities row and OCR waits.
    assert nxt is not None and nxt.feature == "extract" and nxt.document_id == "d2"
