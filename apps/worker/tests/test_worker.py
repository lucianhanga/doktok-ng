"""Worker scan-loop tests with an injected clock (no sleeps)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from doktok_contracts.schemas import JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService
from doktok_worker.worker import IngestionWorker


class FakeMimeDetector:
    def detect(self, path: str) -> str:  # noqa: ARG002
        return "text/plain"


def _services(
    tmp_path: Path, tenant: str, repo: InMemoryIngestionJobRepository
) -> tuple[IngestionServices, FilesystemLayout]:
    layout = FilesystemLayout(tmp_path, tenant)
    layout.ensure()
    services = IngestionServices(
        tenant_id=tenant,
        job_repo=repo,
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMimeDetector(),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
    )
    return services, layout


def _clock(values: list[float]) -> Callable[[], float]:
    it = iter(values)
    return lambda: next(it)


def test_file_ingested_only_after_it_is_stable(tmp_path: Path) -> None:
    repo = InMemoryIngestionJobRepository()
    services, layout = _services(tmp_path, "t1", repo)
    (layout.ingest / "doc.txt").write_bytes(b"content")

    worker = IngestionWorker([services], stability_seconds=3, clock=_clock([0.0, 5.0]))

    assert worker.run_once() == []  # t=0: observed, not yet stable
    second = worker.run_once()  # t=5: stable -> ingested
    assert len(second) == 1
    assert second[0].status is JobStatus.ACTIVE
    assert second[0].tenant_id == "t1"


def test_worker_scans_each_tenant(tmp_path: Path) -> None:
    repo = InMemoryIngestionJobRepository()
    s1, l1 = _services(tmp_path, "t1", repo)
    s2, l2 = _services(tmp_path, "t2", repo)
    (l1.ingest / "a.txt").write_bytes(b"alpha")
    (l2.ingest / "b.txt").write_bytes(b"beta")

    worker = IngestionWorker([s1, s2], stability_seconds=0, clock=_clock([0.0, 1.0]))
    worker.run_once()
    jobs = worker.run_once()

    tenants = {j.tenant_id for j in jobs}
    assert tenants == {"t1", "t2"}


def test_dotfiles_are_ignored(tmp_path: Path) -> None:
    repo = InMemoryIngestionJobRepository()
    services, layout = _services(tmp_path, "t1", repo)
    (layout.ingest / ".DS_Store").write_bytes(b"junk")

    worker = IngestionWorker([services], stability_seconds=0, clock=_clock([0.0, 1.0]))
    worker.run_once()
    assert worker.run_once() == []
