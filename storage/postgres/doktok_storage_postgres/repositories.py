"""PostgreSQL repository adapters. All reads are scoped by tenant_id (ADR-0007)."""

from __future__ import annotations

from typing import Any

from doktok_contracts.schemas import IngestionJob, JobStatus
from psycopg.rows import dict_row
from psycopg.types.json import Json

from doktok_storage_postgres.db import Database

_COLUMNS = (
    "id, tenant_id, document_id, source_path, status, detected_mime, sha256, "
    "error_code, error_message, started_at, finished_at, metadata"
)


def _row_to_job(row: dict[str, Any]) -> IngestionJob:
    return IngestionJob(
        id=row["id"],
        tenant_id=row["tenant_id"],
        document_id=row["document_id"],
        source_path=row["source_path"],
        status=JobStatus(row["status"]),
        detected_mime=row["detected_mime"],
        sha256=row["sha256"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        metadata=row["metadata"] or {},
    )


class PostgresIngestionJobRepository:
    """``IngestionJobRepository`` backed by PostgreSQL. Uses parameterized, tenant-scoped SQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, job: IngestionJob) -> None:
        with self._db.connection() as conn:
            conn.execute(
                f"INSERT INTO ingestion_jobs ({_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    job.id,
                    job.tenant_id,
                    job.document_id,
                    job.source_path,
                    job.status.value,
                    job.detected_mime,
                    job.sha256,
                    job.error_code,
                    job.error_message,
                    job.started_at,
                    job.finished_at,
                    Json(job.metadata),
                ),
            )

    def update(self, job: IngestionJob) -> None:
        # Scope the UPDATE by tenant so a job can never be moved across tenants.
        with self._db.connection() as conn:
            conn.execute(
                "UPDATE ingestion_jobs SET document_id=%s, source_path=%s, status=%s, "
                "detected_mime=%s, sha256=%s, error_code=%s, error_message=%s, "
                "started_at=%s, finished_at=%s, metadata=%s WHERE id=%s AND tenant_id=%s",
                (
                    job.document_id,
                    job.source_path,
                    job.status.value,
                    job.detected_mime,
                    job.sha256,
                    job.error_code,
                    job.error_message,
                    job.started_at,
                    job.finished_at,
                    Json(job.metadata),
                    job.id,
                    job.tenant_id,
                ),
            )

    def get(self, tenant_id: str, job_id: str) -> IngestionJob | None:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            row = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE id=%s AND tenant_id=%s",
                (job_id, tenant_id),
            ).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[IngestionJob]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE tenant_id=%s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (tenant_id, limit, offset),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def find_by_sha256(self, tenant_id: str, sha256: str) -> list[IngestionJob]:
        with self._db.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                f"SELECT {_COLUMNS} FROM ingestion_jobs WHERE tenant_id=%s AND sha256=%s "
                "ORDER BY created_at DESC",
                (tenant_id, sha256),
            ).fetchall()
        return [_row_to_job(row) for row in rows]
