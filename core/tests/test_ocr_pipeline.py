"""OCR pipeline tests (M3) with fake OCR/renderer/builder (no model required)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.media import OcrPageResult, RenderedPage
from doktok_contracts.schemas import JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"


class FakeMime:
    def __init__(self, mime: str) -> None:
        self._mime = mime

    def detect(self, path: str) -> str:  # noqa: ARG002
        return self._mime


class FakeOcr:
    def ocr_image(self, image_png: bytes) -> OcrPageResult:  # noqa: ARG002
        return OcrPageResult(text="OCR TEXT", confidence=0.9)


class FakeRenderer:
    def render_pages(self, path: str, dpi: int = 200) -> list[bytes]:  # noqa: ARG002
        import fitz

        with fitz.open(path) as doc:
            return [b"fake-png" for _ in range(doc.page_count)]


class FakeBuilder:
    def build(self, pages: list[RenderedPage]) -> bytes:  # noqa: ARG002
        return b"%PDF-FAKE-SEARCHABLE"


def _services(tmp_path: Path, mime: str) -> tuple[IngestionServices, FilesystemLayout]:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    services = IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMime(mime),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        ocr_extractor=FakeOcr(),
        pdf_renderer=FakeRenderer(),
        searchable_pdf_builder=FakeBuilder(),
    )
    return services, layout


def _pdf(layout: FilesystemLayout, name: str, page_texts: list[str]) -> str:
    import fitz

    path = layout.ingest / name
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_scanned_pdf_is_ocred_and_searchable(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "application/pdf")
    job = process_file(services, _pdf(layout, "scan.pdf", [""]))  # 1 blank page

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert "OCR TEXT" in (active / "content.md").read_text()
    assert (active / "normalized" / "searchable.pdf").read_bytes() == b"%PDF-FAKE-SEARCHABLE"

    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None
    assert doc.metadata["extraction_method"] == "ocr"
    assert doc.metadata["system_document"] == "normalized/searchable.pdf"


def test_image_is_ocred(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "image/png")
    path = layout.ingest / "pic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    job = process_file(services, str(path))

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert "OCR TEXT" in (active / "content.md").read_text()
    assert (active / "normalized" / "searchable.pdf").exists()
    assert (active / "original.png").exists()


def test_mixed_pdf_keeps_embedded_text_and_ocrs_blanks(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "application/pdf")
    job = process_file(services, _pdf(layout, "mixed.pdf", ["Real embedded text", ""]))

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    content = (active / "content.md").read_text()
    assert "Real embedded text" in content  # embedded text not destroyed
    assert "OCR TEXT" in content  # blank page OCR'd
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "pdf_mixed"
