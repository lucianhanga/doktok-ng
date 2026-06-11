"""In-memory ingestion job repository for tests and local/dev runs without a database.

Reads are tenant-scoped to mirror the Postgres adapter (ADR-0007).
"""

from __future__ import annotations

from datetime import datetime

from doktok_contracts.schemas import IngestionJob, JobStatus


class InMemoryIngestionJobRepository:
    """An ``IngestionJobRepository`` kept entirely in memory (insertion-ordered)."""

    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJob] = {}

    def add(self, job: IngestionJob) -> None:
        if job.id in self._jobs:
            raise ValueError(f"job {job.id} already exists")
        self._jobs[job.id] = job.model_copy(deep=True)

    def update(self, job: IngestionJob) -> None:
        existing = self._jobs.get(job.id)
        if existing is None:
            raise KeyError(job.id)
        if existing.tenant_id != job.tenant_id:
            raise PermissionError("cannot move a job across tenants")
        self._jobs[job.id] = job.model_copy(deep=True)

    def get(self, tenant_id: str, job_id: str) -> IngestionJob | None:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            return None
        return job.model_copy(deep=True)

    def list_jobs(self, tenant_id: str, limit: int = 50, offset: int = 0) -> list[IngestionJob]:
        # Newest first, mirroring the Postgres adapter's created_at DESC ordering.
        jobs = [job for job in reversed(self._jobs.values()) if job.tenant_id == tenant_id]
        return [job.model_copy(deep=True) for job in jobs[offset : offset + limit]]

    def find_by_sha256(self, tenant_id: str, sha256: str) -> list[IngestionJob]:
        return [
            job.model_copy(deep=True)
            for job in reversed(self._jobs.values())
            if job.tenant_id == tenant_id and job.sha256 == sha256
        ]

    def delete_failed_for_sha(self, tenant_id: str, sha256: str) -> int:
        victims = [
            jid
            for jid, job in self._jobs.items()
            if job.tenant_id == tenant_id
            and job.sha256 == sha256
            and job.status is JobStatus.FAILED
        ]
        for jid in victims:
            del self._jobs[jid]
        return len(victims)

    def delete_for_sha(self, tenant_id: str, sha256: str) -> int:
        victims = [
            jid
            for jid, job in self._jobs.items()
            if job.tenant_id == tenant_id and job.sha256 == sha256
        ]
        for jid in victims:
            del self._jobs[jid]
        return len(victims)

    def list_in_flight(self, tenant_id: str, *, before: datetime) -> list[IngestionJob]:
        terminal = {
            JobStatus.ACTIVE,
            JobStatus.FAILED,
            JobStatus.QUARANTINED,
            JobStatus.DUPLICATE,
        }
        return [
            job.model_copy(deep=True)
            for job in self._jobs.values()
            if job.tenant_id == tenant_id
            and job.status not in terminal
            and job.started_at is not None
            and job.started_at < before
        ]

    def delete(self, tenant_id: str, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.tenant_id == tenant_id:
            del self._jobs[job_id]
