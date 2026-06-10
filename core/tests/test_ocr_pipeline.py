"""OCR pipeline tests (M3 + LLM judge) with fakes (no model required)."""

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


class FakeClassifier:
    def __init__(self, coverages: list[float]) -> None:
        self._coverages = coverages

    def page_image_coverage(self, path: str) -> list[float]:  # noqa: ARG002
        return self._coverages


class FakeChat:
    """An LLM judge stub that always replies with a fixed verdict ('A' or 'B')."""

    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return self._reply


def _services(
    tmp_path: Path,
    mime: str,
    *,
    coverages: list[float] | None = None,
    coverage_threshold: float = 1.0,
    min_text_quality: float = 0.0,
    chat: FakeChat | None = None,
) -> tuple[IngestionServices, FilesystemLayout]:
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
        pdf_classifier=FakeClassifier(coverages) if coverages is not None else None,
        ocr_image_coverage=coverage_threshold,
        ocr_min_text_quality=min_text_quality,
        chat_model=chat,
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


def _content(layout: FilesystemLayout, document_id: str | None) -> str:
    assert document_id is not None
    return (layout.active_dir(document_id) / "content.md").read_text()


def test_scanned_pdf_is_ocred_and_searchable(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "application/pdf")
    job = process_file(services, _pdf(layout, "scan.pdf", [""]))  # 1 blank page

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert "OCR TEXT" in (active / "content.md").read_text()
    assert (active / "normalized" / "searchable.pdf").read_bytes() == b"%PDF-FAKE-SEARCHABLE"
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "ocr"


def test_image_is_ocred(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "image/png")
    path = layout.ingest / "pic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    job = process_file(services, str(path))

    assert job.status is JobStatus.ACTIVE
    active = layout.active_dir(job.document_id)  # type: ignore[arg-type]
    assert "OCR TEXT" in (active / "content.md").read_text()
    assert (active / "original.png").exists()


def test_born_digital_pages_keep_embedded_text(tmp_path: Path) -> None:
    services, layout = _services(tmp_path, "application/pdf")  # no classifier -> coverage 0
    job = process_file(services, _pdf(layout, "doc.pdf", ["Real embedded text", "more text"]))
    content = _content(layout, job.document_id)
    assert "Real embedded text" in content and "OCR TEXT" not in content
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "pdf_text"


def test_good_embedded_layer_kept_via_quality_gate(tmp_path: Path) -> None:
    # Full-page image whose embedded text is clean -> fast path keeps it (no OCR), the user's case.
    services, layout = _services(
        tmp_path, "application/pdf", coverages=[1.0], coverage_threshold=0.8, min_text_quality=0.5
    )
    job = process_file(
        services, _pdf(layout, "good.pdf", ["This is a clean readable paragraph of real text."])
    )
    content = _content(layout, job.document_id)
    assert "clean readable paragraph" in content and "OCR TEXT" not in content
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "pdf_text"


def test_garbled_embedded_layer_is_reocred_by_heuristic(tmp_path: Path) -> None:
    # No LLM: heuristic prefers the cleaner OCR text over a garbled embedded layer.
    services, layout = _services(
        tmp_path, "application/pdf", coverages=[1.0], coverage_threshold=0.8, min_text_quality=0.5
    )
    job = process_file(services, _pdf(layout, "garbled.pdf", ["q3 !! @@ ## $$ %% z9"]))
    content = _content(layout, job.document_id)
    assert "OCR TEXT" in content
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "ocr"


def test_llm_judge_keeps_embedded_when_it_prefers_it(tmp_path: Path) -> None:
    # LLM replies "A" -> keep the embedded text even though the page is a full-page image.
    services, layout = _services(
        tmp_path, "application/pdf", coverages=[1.0], coverage_threshold=0.8, chat=FakeChat("A")
    )
    job = process_file(services, _pdf(layout, "judge.pdf", ["the original embedded layer text"]))
    content = _content(layout, job.document_id)
    assert "original embedded layer" in content and "OCR TEXT" not in content
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "pdf_text"


def test_llm_judge_picks_ocr_when_it_prefers_it(tmp_path: Path) -> None:
    services, layout = _services(
        tmp_path, "application/pdf", coverages=[1.0], coverage_threshold=0.8, chat=FakeChat("B")
    )
    job = process_file(services, _pdf(layout, "judge.pdf", ["the original embedded layer text"]))
    content = _content(layout, job.document_id)
    assert "OCR TEXT" in content and "original embedded layer" not in content
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None and doc.metadata["extraction_method"] == "ocr"
