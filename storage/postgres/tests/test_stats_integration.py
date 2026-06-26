"""Integration test for the Postgres stats repository (test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime

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
    assert summary.jobs == {"active": 1, "failed": 1}
    assert summary.entities == 1
    # The pending feature is counted as in-progress, not as "needs attention" (failed).
    assert summary.documents_processing_features == 1
    assert summary.documents_pending_features == 0
