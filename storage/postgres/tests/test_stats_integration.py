"""Integration test for the Postgres stats repository (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import (
    Document,
    DocumentEntity,
    DocumentStatus,
    EntityType,
    IngestionJob,
    JobStatus,
)
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresStatsRepository,
)

TENANT = "test-a"


def test_summary_counts(db: Database) -> None:
    docs = PostgresDocumentRepository(db)
    docs.add(
        Document(
            id="d1",
            tenant_id=TENANT,
            sha256="a" * 64,
            original_filename="d1.txt",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )
    jobs = PostgresIngestionJobRepository(db)
    jobs.add(IngestionJob(id="j1", tenant_id=TENANT, source_path="/x", status=JobStatus.ACTIVE))
    jobs.add(IngestionJob(id="j2", tenant_id=TENANT, source_path="/y", status=JobStatus.FAILED))
    jobs.add(
        IngestionJob(
            id="j3",
            tenant_id=TENANT,
            source_path="/z",
            status=JobStatus.FAILED,
            error_code="worker_crash_recovered",  # tripwire tombstone: not an actionable failure
        )
    )
    PostgresEntityRepository(db).add_entities(
        [
            DocumentEntity(
                id="e1",
                tenant_id=TENANT,
                document_id="d1",
                version_id="",
                entity_text="a@b.com",
                entity_type=EntityType.EMAIL,
                normalized_value="a@b.com",
                frequency=1,
            )
        ]
    )

    # Seed a feature row for the active doc -> it is queued ('pending'), i.e. work in flight.
    PostgresFeatureRepository(db).ensure_for_active(TENANT, [("structured_records", 1)])

    summary = PostgresStatsRepository(db).summary(TENANT)
    assert summary.documents == 1
    # The worker_crash_recovered tombstone (j3) is excluded from the job-status counts.
    assert summary.jobs == {"active": 1, "failed": 1}
    assert summary.entities == 1
    # The pending feature is counted as in-progress, not as "needs attention" (failed).
    assert summary.documents_processing_features == 1
    assert summary.documents_pending_features == 0


def test_summary_reports_per_stage_queue_counts(db: Database) -> None:
    # The overview's stage-queue section (#848) reads per-feature pending/running/failed counts.
    docs = PostgresDocumentRepository(db)
    docs.add(
        Document(
            id="d1",
            tenant_id=TENANT,
            sha256="s" * 64,
            original_filename="d1.txt",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )
    features = PostgresFeatureRepository(db)
    features.seed_for_document(TENANT, "d1", [("extract", 1), ("entities", 1)])
    now = datetime.now(UTC)
    claimed = features.claim_next(TENANT, now=now, reclaim_before=now - timedelta(seconds=900))
    assert claimed is not None

    summary = PostgresStatsRepository(db).summary(TENANT)
    queues = {q.feature: q for q in summary.stage_queues}
    assert queues[claimed.feature].running == 1
    assert queues[claimed.feature].queued == 0
    other = "entities" if claimed.feature == "extract" else "extract"
    assert queues[other].queued == 1


def test_stage_queues_split_claimable_from_blocked(db: Database) -> None:
    # A document queues for OCR FIRST: downstream stages with unfinished prerequisites are
    # "blocked", not "queued" - 'Relations: 10 queued' was nonsense when nothing could run them.
    docs = PostgresDocumentRepository(db)
    docs.add(
        Document(
            id="d1",
            tenant_id=TENANT,
            sha256="b" * 64,
            original_filename="d1.txt",
            status=DocumentStatus.PROCESSING,
            created_at=datetime.now(UTC),
        )
    )
    PostgresFeatureRepository(db).seed_for_document(TENANT, "d1", [("extract", 1), ("entities", 1)])

    deps = [("entities", "extract")]
    summary = PostgresStatsRepository(db).summary(TENANT, dependencies=deps)
    queues = {q.feature: q for q in summary.stage_queues}
    assert queues["extract"].queued == 1 and queues["extract"].blocked == 0
    # entities waits on extract: not queued, blocked instead
    assert queues["entities"].queued == 0 and queues["entities"].blocked == 1

    # Once extract completes, entities becomes claimable.
    features = PostgresFeatureRepository(db)
    now = datetime.now(UTC)
    claimed = features.claim_next(TENANT, now=now, reclaim_before=now - timedelta(seconds=900))
    assert claimed is not None and claimed.feature == "extract"
    features.mark_done(claimed.id, feature_version=1)
    summary = PostgresStatsRepository(db).summary(TENANT, dependencies=deps)
    queues = {q.feature: q for q in summary.stage_queues}
    assert queues["entities"].queued == 1 and queues["entities"].blocked == 0
