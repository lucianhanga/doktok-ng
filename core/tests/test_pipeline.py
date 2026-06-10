"""End-to-end pipeline tests using real filesystem + extractor adapters and a fake MIME detector."""

from __future__ import annotations

import json
from pathlib import Path

from doktok_contracts.schemas import JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"


class FakeMimeDetector:
    def __init__(self, mime: str) -> None:
        self._mime = mime

    def detect(self, path: str) -> str:  # noqa: ARG002 - fixed mime for tests
        return self._mime


def build_services(
    tmp_path: Path,
    *,
    mime: str,
    tenant: str = TENANT,
    job_repo: InMemoryIngestionJobRepository | None = None,
    document_repo: InMemoryDocumentRepository | None = None,
) -> tuple[IngestionServices, FilesystemLayout]:
    layout = FilesystemLayout(tmp_path, tenant)
    layout.ensure()
    services = IngestionServices(
        tenant_id=tenant,
        job_repo=job_repo or InMemoryIngestionJobRepository(),
        document_repo=document_repo or InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMimeDetector(mime),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
    )
    return services, layout


def drop(layout: FilesystemLayout, name: str, content: bytes) -> str:
    path = layout.ingest / name
    path.write_bytes(content)
    return str(path)


def make_pdf(layout: FilesystemLayout, name: str, text: str) -> str:
    import fitz

    path = layout.ingest / name
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_text_file_becomes_active_document(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="text/plain")
    job = process_file(services, drop(layout, "note.txt", b"hello world"))

    assert job.status is JobStatus.ACTIVE
    assert job.document_id is not None

    document = services.document_repo.get(TENANT, job.document_id)
    assert document is not None
    assert document.original_filename == "note.txt"
    assert document.status.value == "active"

    active = layout.active_dir(job.document_id)
    # original keeps its extension and is the canonical "system document"
    assert (active / "original.txt").read_bytes() == b"hello world"
    assert (active / "content.md").read_text() == "hello world"
    manifest = json.loads((active / "manifest.json").read_text())
    assert manifest["extraction_method"] == "text"
    assert manifest["system_document"] == "original.txt"
    assert manifest["artifacts"]["original"] == "original.txt"
    assert manifest["artifacts"]["normalized_pdf"] is None
    assert not layout.job_workdir(job.id).exists()  # working dir cleaned up


def test_markdown_preserves_content(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="text/markdown")
    job = process_file(services, drop(layout, "doc.md", b"# Title\n\nBody"))

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert (active / "content.md").read_text() == "# Title\n\nBody"


def test_born_digital_pdf_is_extracted(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/pdf")
    job = process_file(services, make_pdf(layout, "report.pdf", "Quarterly numbers"))

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert (active / "original.pdf").exists()
    assert "Quarterly numbers" in (active / "content.md").read_text()


def test_scanned_pdf_needs_ocr(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/pdf")
    job = process_file(services, make_pdf(layout, "scan.pdf", ""))  # no embedded text

    assert job.status is JobStatus.FAILED
    assert job.error_code == "needs_ocr"
    assert layout.failed_dir(job.id).exists()


def test_image_needs_ocr(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="image/png")
    job = process_file(services, drop(layout, "pic.png", b"\x89PNG\r\n"))

    assert job.status is JobStatus.FAILED
    assert job.error_code == "needs_ocr"


def test_unsupported_file_goes_to_failed(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/octet-stream")
    job = process_file(services, drop(layout, "blob.bin", b"\x00\x01\x02"))

    assert job.status is JobStatus.FAILED
    assert job.error_code == "unsupported_type"


def test_dangerous_file_is_quarantined(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="application/x-dosexec")
    job = process_file(services, drop(layout, "evil.exe", b"MZ\x90\x00"))

    assert job.status is JobStatus.QUARANTINED
    assert (layout.quarantine / job.id).exists()


def test_duplicate_hash_is_handled(tmp_path: Path) -> None:
    services, layout = build_services(tmp_path, mime="text/plain")
    first = process_file(services, drop(layout, "a.txt", b"same content"))
    second = process_file(services, drop(layout, "b.txt", b"same content"))

    assert first.status is JobStatus.ACTIVE
    assert second.status is JobStatus.FAILED
    assert second.error_code == "duplicate_hash"


def test_dedup_is_per_tenant(tmp_path: Path) -> None:
    job_repo = InMemoryIngestionJobRepository()
    doc_repo = InMemoryDocumentRepository()
    s1, l1 = build_services(
        tmp_path, mime="text/plain", tenant="t1", job_repo=job_repo, document_repo=doc_repo
    )
    s2, l2 = build_services(
        tmp_path, mime="text/plain", tenant="t2", job_repo=job_repo, document_repo=doc_repo
    )

    j1 = process_file(s1, drop(l1, "a.txt", b"shared"))
    j2 = process_file(s2, drop(l2, "a.txt", b"shared"))

    assert j1.status is JobStatus.ACTIVE and j2.status is JobStatus.ACTIVE
    assert j1.tenant_id == "t1" and j2.tenant_id == "t2"
