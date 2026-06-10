"""Ingestion pipeline orchestration (M1).

Coordinates the early lifecycle of a dropped file using ports only (ADR-0001, ADR-0004):

    move to in.process/{job_id}/source -> hash -> detect MIME -> dedup -> security decision

Outcomes:
- ALLOW       -> job parked at ``normalizing`` (validated, awaiting extraction in M2)
- duplicate   -> job ``failed`` (error_code ``duplicate_hash``), workdir moved to docs.failed/
- REJECT      -> job ``failed`` (``unsupported_type`` / ``too_large``), workdir -> docs.failed/
- QUARANTINE  -> job ``quarantined``, workdir moved to quarantine/

Extraction, chunking, embedding, and activation arrive in later milestones. This stage never marks a
document active.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.ports import (
    FileStorage,
    HashService,
    IngestionJobRepository,
    MimeDetector,
    QuarantineService,
    SecurityPolicy,
)
from doktok_contracts.schemas import IngestionJob, JobStatus, SecurityDecision

from doktok_core.ingestion.layout import FilesystemLayout


@dataclass
class IngestionServices:
    """Ports + layout the pipeline depends on (wired per tenant at the composition root)."""

    tenant_id: str
    job_repo: IngestionJobRepository
    file_storage: FileStorage
    hash_service: HashService
    mime_detector: MimeDetector
    security_policy: SecurityPolicy
    quarantine_service: QuarantineService
    layout: FilesystemLayout


def _new_job_id() -> str:
    return uuid.uuid4().hex


def process_file(services: IngestionServices, source_path: str) -> IngestionJob:
    """Run the M1 ingestion stage for a single stable file. Returns the resulting job."""
    job_id = _new_job_id()
    now = datetime.now(UTC)
    original_path = str(source_path)
    job = IngestionJob(
        id=job_id,
        tenant_id=services.tenant_id,
        source_path=original_path,
        status=JobStatus.QUEUED,
        started_at=now,
        metadata={"original_ingest_path": original_path},
    )
    services.job_repo.add(job)

    workdir = services.layout.job_workdir(job_id)
    try:
        # Atomically claim the file into the job's working directory.
        dest = services.layout.job_source(job_id)
        job.status = JobStatus.DETECTING
        services.file_storage.move(original_path, str(dest))
        job.source_path = str(dest)

        # Hash for deduplication and provenance.
        job.status = JobStatus.HASHING
        job.sha256 = services.hash_service.sha256(str(dest))

        # Detect MIME by content (never by extension).
        job.detected_mime = services.mime_detector.detect(str(dest))
        services.job_repo.update(job)

        if _is_duplicate(services, job):
            return _fail(
                services,
                job,
                workdir,
                code="duplicate_hash",
                message=f"content with sha256 {job.sha256} has already been ingested",
            )

        size_bytes = os.path.getsize(dest)
        decision = services.security_policy.decide(job.detected_mime, size_bytes)
        if decision is SecurityDecision.QUARANTINE:
            return _quarantine(services, job, workdir)
        if decision is SecurityDecision.REJECT:
            max_bytes = getattr(services.security_policy, "max_file_bytes", None)
            too_large = max_bytes is not None and size_bytes > max_bytes
            return _fail(
                services,
                job,
                workdir,
                code="too_large" if too_large else "unsupported_type",
                message=f"rejected mime={job.detected_mime} size={size_bytes}",
            )

        # ALLOW: validated and parked, awaiting extraction (M2).
        job.status = JobStatus.NORMALIZING
        services.job_repo.update(job)
        return job
    except Exception as exc:  # noqa: BLE001 - record any failure on the job, do not crash the worker
        return _fail(
            services,
            job,
            workdir,
            code="internal_error",
            message=str(exc),
        )


def _is_duplicate(services: IngestionServices, job: IngestionJob) -> bool:
    if not job.sha256:
        return False
    for other in services.job_repo.find_by_sha256(services.tenant_id, job.sha256):
        if other.id == job.id:
            continue
        if other.status not in (JobStatus.FAILED, JobStatus.QUARANTINED):
            return True
    return False


def _fail(
    services: IngestionServices,
    job: IngestionJob,
    workdir: Path,
    *,
    code: str,
    message: str,
) -> IngestionJob:
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.finished_at = datetime.now(UTC)
    _safe_move(services, workdir, services.layout.failed_dir(job.id))
    services.job_repo.update(job)
    return job


def _quarantine(services: IngestionServices, job: IngestionJob, workdir: Path) -> IngestionJob:
    job.status = JobStatus.QUARANTINED
    job.error_code = "quarantined"
    job.error_message = f"disallowed content type {job.detected_mime}"
    job.finished_at = datetime.now(UTC)
    if workdir.exists():
        services.quarantine_service.quarantine(str(workdir), reason=job.error_message)
    services.job_repo.update(job)
    return job


def _safe_move(services: IngestionServices, source: Path, destination: Path) -> None:
    if source.exists():
        services.file_storage.move(str(source), str(destination))
