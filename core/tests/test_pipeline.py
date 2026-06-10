"""End-to-end pipeline tests using real filesystem adapters and a fake MIME detector."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.schemas import JobStatus
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService


class FakeMimeDetector:
    def __init__(self, mime: str) -> None:
        self._mime = mime

    def detect(self, path: str) -> str:  # noqa: ARG002 - fixed mime for tests
        return self._mime


def build_services(tmp_path: Path, *, mime: str) -> tuple[IngestionServices, FilesystemLayout]:
    layout = FilesystemLayout(tmp_path)
    layout.ensure()
    repo = InMemoryIngestionJobRepository()
    services = IngestionServices(
        job_repo=repo,
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMimeDetector(mime),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        layout=layout,
    )
    return services, layout


def drop(layout: FilesystemLayout, name: str, content: bytes) -> str:
    path = layout.ingest / name
    path.write_bytes(content)
    return str(path)


def test_supported_file_is_parked_for_extraction(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="text/plain")
    source = drop(layout, "note.txt", b"hello world")

    job = process_file(services, source)

    assert job.status is JobStatus.NORMALIZING
    assert job.detected_mime == "text/plain"
    assert job.sha256 is not None and len(job.sha256) == 64
    assert not Path(source).exists()  # moved out of ingest
    assert (layout.job_source(job.id)).read_bytes() == b"hello world"
    assert services.job_repo.get(job.id) is not None


def test_unsupported_file_goes_to_failed(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/octet-stream")
    source = drop(layout, "blob.bin", b"\x00\x01\x02")

    job = process_file(services, source)

    assert job.status is JobStatus.FAILED
    assert job.error_code == "unsupported_type"
    assert layout.failed_dir(job.id).exists()
    assert not layout.job_workdir(job.id).exists()


def test_dangerous_file_is_quarantined(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/x-dosexec")
    source = drop(layout, "evil.exe", b"MZ\x90\x00")

    job = process_file(services, source)

    assert job.status is JobStatus.QUARANTINED
    assert (layout.quarantine / job.id).exists()


def test_duplicate_hash_is_handled(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="text/plain")
    first = process_file(services, drop(layout, "a.txt", b"same content"))
    second = process_file(services, drop(layout, "b.txt", b"same content"))

    assert first.status is JobStatus.NORMALIZING
    assert second.status is JobStatus.FAILED
    assert second.error_code == "duplicate_hash"
    assert first.sha256 == second.sha256
