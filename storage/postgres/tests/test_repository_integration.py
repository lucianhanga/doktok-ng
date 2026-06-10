"""Integration tests for the Postgres ingestion repository.

Uses only ``test*`` tenants; cleanup is scoped in conftest so other tenants are never touched.
Skipped automatically when no database is reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_storage_postgres import Database, PostgresIngestionJobRepository

# Tenant ids start with "test" so conftest cleanup matches them (and nothing else).
TEST_TENANT = "test"
TEST_TENANT_A = "test-a"
TEST_TENANT_B = "test-b"


def _job(
    job_id: str, sha: str, *, tenant: str = TEST_TENANT, status: JobStatus = JobStatus.QUEUED
) -> IngestionJob:
    return IngestionJob(
        id=job_id,
        tenant_id=tenant,
        source_path=f"/ingest/{job_id}",
        status=status,
        detected_mime="text/plain",
        sha256=sha,
        started_at=datetime.now(UTC),
        metadata={"k": "v"},
    )


def test_add_and_get_roundtrip(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    repo.add(_job("j1", "a" * 64))

    fetched = repo.get(TEST_TENANT, "j1")
    assert fetched is not None
    assert fetched.sha256 == "a" * 64
    assert fetched.metadata == {"k": "v"}


def test_update_and_find_by_sha256(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    job = _job("j2", "c" * 64)
    repo.add(job)

    job.status = JobStatus.NORMALIZING
    repo.update(job)

    assert repo.get(TEST_TENANT, "j2").status is JobStatus.NORMALIZING  # type: ignore[union-attr]
    found = repo.find_by_sha256(TEST_TENANT, "c" * 64)
    assert [j.id for j in found] == ["j2"]


def test_list_returns_newest_first(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    repo.add(_job("old", "1" * 64))
    repo.add(_job("new", "2" * 64))
    ids = [j.id for j in repo.list_jobs(TEST_TENANT, limit=10)]
    assert ids[0] == "new"


def test_tenant_isolation(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    repo.add(_job("ta-job", "f" * 64, tenant=TEST_TENANT_A))
    repo.add(_job("tb-job", "f" * 64, tenant=TEST_TENANT_B))

    assert [j.id for j in repo.list_jobs(TEST_TENANT_A)] == ["ta-job"]
    assert [j.id for j in repo.list_jobs(TEST_TENANT_B)] == ["tb-job"]
    assert repo.get(TEST_TENANT_A, "tb-job") is None
    assert [j.id for j in repo.find_by_sha256(TEST_TENANT_A, "f" * 64)] == ["ta-job"]
