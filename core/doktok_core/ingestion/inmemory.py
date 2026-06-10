"""In-memory ingestion job repository for tests and local/dev runs without a database."""

from __future__ import annotations

from doktok_contracts.schemas import IngestionJob


class InMemoryIngestionJobRepository:
    """An ``IngestionJobRepository`` kept entirely in memory (insertion-ordered)."""

    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJob] = {}

    def add(self, job: IngestionJob) -> None:
        if job.id in self._jobs:
            raise ValueError(f"job {job.id} already exists")
        self._jobs[job.id] = job.model_copy(deep=True)

    def update(self, job: IngestionJob) -> None:
        if job.id not in self._jobs:
            raise KeyError(job.id)
        self._jobs[job.id] = job.model_copy(deep=True)

    def get(self, job_id: str) -> IngestionJob | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    def list_jobs(self, limit: int = 50, offset: int = 0) -> list[IngestionJob]:
        # Newest first, mirroring the Postgres adapter's created_at DESC ordering.
        jobs = list(reversed(self._jobs.values()))
        return [job.model_copy(deep=True) for job in jobs[offset : offset + limit]]

    def find_by_sha256(self, sha256: str) -> list[IngestionJob]:
        return [
            job.model_copy(deep=True)
            for job in reversed(self._jobs.values())
            if job.sha256 == sha256
        ]
