"""Recovery of ingestion jobs abandoned mid-pipeline by a killed worker (no DB/Ollama)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, recover_stale_jobs
from doktok_storage_filesystem import LocalFileStorage

TENANT = "t1"


def _services(tmp_path: Path) -> IngestionServices:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    return IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=None,  # type: ignore[arg-type]
        mime_detector=None,  # type: ignore[arg-type]
        security_policy=None,  # type: ignore[arg-type]
        quarantine_service=None,  # type: ignore[arg-type]
        text_extractor=None,  # type: ignore[arg-type]
        pdf_extractor=None,  # type: ignore[arg-type]
        layout=layout,
    )


def _stranded_job(services: IngestionServices, *, age_minutes: int, name: str) -> IngestionJob:
    """Simulate a worker that was killed after moving the file into its in.process workdir."""
    job_id = "job0000000000000000000000000000ab"
    workdir = services.layout.job_workdir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)
    source = services.layout.job_source(job_id, ".pdf")
    source.write_bytes(b"%PDF-1.4 stranded\n")
    job = IngestionJob(
        id=job_id,
        tenant_id=TENANT,
        source_path=str(source),
        status=JobStatus.EXTRACTING,
        started_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
        metadata={"original_ingest_path": str(services.layout.ingest / name)},
    )
    services.job_repo.add(job)
    return job


def test_stale_job_is_requeued_to_ingest(tmp_path: Path) -> None:
    services = _services(tmp_path)
    _stranded_job(services, age_minutes=30, name="0000046.pdf")

    recovered = recover_stale_jobs(services, older_than=datetime.now(UTC) - timedelta(minutes=10))

    assert len(recovered) == 1
    # File is back in the ingest folder under its original name, ready to be picked up again.
    assert (services.layout.ingest / "0000046.pdf").is_file()
    # The stale job row stays as a FAILED tombstone (crash-strike bookkeeping); the working dir
    # is gone and the file is ready to be picked up again by the normal scan.
    (tombstone,) = services.job_repo.list_jobs(TENANT)
    assert tombstone.status is JobStatus.FAILED
    assert tombstone.error_code == "worker_crash_recovered"
    assert not services.layout.job_workdir(recovered[0].id).exists()


def test_recent_in_flight_job_is_left_alone(tmp_path: Path) -> None:
    # A job younger than the cutoff may still be actively processing - it must not be touched.
    services = _services(tmp_path)
    _stranded_job(services, age_minutes=2, name="0000051.pdf")

    recovered = recover_stale_jobs(services, older_than=datetime.now(UTC) - timedelta(minutes=10))

    assert recovered == []
    assert len(services.job_repo.list_jobs(TENANT)) == 1
    assert not (services.layout.ingest / "0000051.pdf").exists()


def _tombstone(services: IngestionServices, sha256: str, suffix: str) -> None:
    """A prior recovery tombstone for the same content (job failed as worker_crash_recovered)."""
    services.job_repo.add(
        IngestionJob(
            id=f"tomb{suffix}00000000000000000000000000cd",
            tenant_id=TENANT,
            source_path=f"/gone/{suffix}.pdf",
            status=JobStatus.FAILED,
            started_at=datetime.now(UTC) - timedelta(hours=1),
            sha256=sha256,
            error_code="worker_crash_recovered",
            error_message="abandoned mid-pipeline (worker died); file re-queued",
            finished_at=datetime.now(UTC) - timedelta(minutes=50),
            metadata={},
        )
    )


def test_repeat_crash_file_is_quarantined_not_requeued(tmp_path: Path) -> None:
    services = _services(tmp_path)
    sha = "deadbeef" * 8
    _tombstone(services, sha, "a")
    _tombstone(services, sha, "b")
    job = _stranded_job(services, age_minutes=30, name="poison.pdf")
    job.sha256 = sha
    services.job_repo.update(job)

    recovered = recover_stale_jobs(services, older_than=datetime.now(UTC) - timedelta(minutes=10))

    assert len(recovered) == 1
    final = services.job_repo.get(TENANT, job.id)
    assert final is not None
    assert final.status is JobStatus.QUARANTINED
    assert final.error_code == "crash_loop"
    # The poison file lands in quarantine, NOT back in ingest (no 4th crash).
    assert not (services.layout.ingest / "poison.pdf").exists()
    assert (services.layout.quarantine_dir(job.id) / "poison.pdf").is_file()


def test_first_strike_still_requeues(tmp_path: Path) -> None:
    services = _services(tmp_path)
    sha = "cafef00d" * 8
    _tombstone(services, sha, "a")
    job = _stranded_job(services, age_minutes=30, name="flaky.pdf")
    job.sha256 = sha
    services.job_repo.update(job)

    recover_stale_jobs(services, older_than=datetime.now(UTC) - timedelta(minutes=10))

    assert (services.layout.ingest / "flaky.pdf").is_file()
