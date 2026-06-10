"""Integration tests for the Postgres ingestion repository.

Skipped automatically when no database is reachable, so the unit suite stays green locally without
Docker. CI provides a pgvector service and runs these for real.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest
from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_storage_postgres import Database, PostgresIngestionJobRepository, migrate

DSN = os.environ.get("DOKTOK_DATABASE_URL", "postgresql://doktok:doktok@localhost:5432/doktok")


@pytest.fixture
def db() -> Iterator[Database]:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres not reachable: {exc}")
    database = Database(DSN)
    migrate(database)
    with database.connection() as conn:
        conn.execute("TRUNCATE ingestion_jobs")
    yield database
    database.close()


def _job(
    job_id: str, sha: str, *, tenant: str = "t1", status: JobStatus = JobStatus.QUEUED
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

    fetched = repo.get("t1", "j1")
    assert fetched is not None
    assert fetched.tenant_id == "t1"
    assert fetched.sha256 == "a" * 64
    assert fetched.metadata == {"k": "v"}


def test_update_and_find_by_sha256(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    job = _job("j2", "c" * 64)
    repo.add(job)

    job.status = JobStatus.NORMALIZING
    repo.update(job)

    assert repo.get("t1", "j2").status is JobStatus.NORMALIZING  # type: ignore[union-attr]
    found = repo.find_by_sha256("t1", "c" * 64)
    assert [j.id for j in found] == ["j2"]


def test_list_returns_newest_first(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    repo.add(_job("old", "1" * 64))
    repo.add(_job("new", "2" * 64))
    ids = [j.id for j in repo.list_jobs("t1", limit=10)]
    assert ids[0] == "new"


def test_tenant_isolation(db: Database) -> None:
    repo = PostgresIngestionJobRepository(db)
    repo.add(_job("ta-job", "f" * 64, tenant="tenant-a"))
    repo.add(_job("tb-job", "f" * 64, tenant="tenant-b"))

    # Same sha256 across tenants must not collide, and reads are scoped.
    assert [j.id for j in repo.list_jobs("tenant-a")] == ["ta-job"]
    assert [j.id for j in repo.list_jobs("tenant-b")] == ["tb-job"]
    assert repo.get("tenant-a", "tb-job") is None
    assert [j.id for j in repo.find_by_sha256("tenant-a", "f" * 64)] == ["ta-job"]
