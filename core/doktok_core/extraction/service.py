"""Extraction routing (M2 + M3).

Maps a MIME type to extracted content, transparently falling back to OCR when there is no embedded
text and OCR services are available:

- text/plain, text/markdown        -> direct text
- born-digital PDF (text pages)    -> embedded text (PyMuPDF)
- PDF page with no text OR a full-page image (>= coverage threshold) -> OCR that page, dropping any
  existing (possibly weak) embedded text layer; other pages keep their embedded text
- image                            -> OCR (M3)

Returns the extraction result and an optional ``normalized/searchable.pdf`` (images + OCR text
layer), produced for fully OCR'd input. If OCR is needed but unavailable, raises ``NeedsOcrError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from doktok_contracts.media import RenderedPage
from doktok_contracts.ports import (
    OcrExtractor,
    PdfClassifier,
    PdfRenderer,
    PdfTextExtractor,
    SearchablePdfBuilder,
    TextExtractor,
)

MIME_TEXT = "text/plain"
MIME_MARKDOWN = "text/markdown"
MIME_PDF = "application/pdf"


class NeedsOcrError(Exception):
    """Raised when a file needs OCR but no OCR services are configured."""


@dataclass
class ExtractionResult:
    content_md: str
    pages: list[str]
    extraction_method: str
    page_count: int
    ocr_confidence: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


def _pdf_markdown(pages: list[str]) -> str:
    return "\n\n".join(f"## Page {i}\n\n{page.strip()}" for i, page in enumerate(pages, start=1))


def _average(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def extract_document(
    mime: str,
    path: str,
    *,
    text_extractor: TextExtractor,
    pdf_extractor: PdfTextExtractor,
    ocr: OcrExtractor | None = None,
    renderer: PdfRenderer | None = None,
    builder: SearchablePdfBuilder | None = None,
    classifier: PdfClassifier | None = None,
    ocr_image_coverage: float = 1.0,
) -> tuple[ExtractionResult, bytes | None]:
    if mime in (MIME_TEXT, MIME_MARKDOWN):
        text = text_extractor.extract(path)
        method = "markdown" if mime == MIME_MARKDOWN else "text"
        return ExtractionResult(text, [text], method, 1), None

    if mime == MIME_PDF:
        return _extract_pdf(
            path, pdf_extractor, ocr, renderer, builder, classifier, ocr_image_coverage
        )

    if mime.startswith("image/"):
        if ocr is None:
            raise NeedsOcrError(f"{mime} requires OCR")
        image = Path(path).read_bytes()
        page = ocr.ocr_image(image)
        result = ExtractionResult(page.text, [page.text], "ocr", 1, page.confidence)
        normalized = builder.build([RenderedPage(image, page.text)]) if builder else None
        return result, normalized

    raise NeedsOcrError(f"no extractor for {mime}")


def _extract_pdf(
    path: str,
    pdf_extractor: PdfTextExtractor,
    ocr: OcrExtractor | None,
    renderer: PdfRenderer | None,
    builder: SearchablePdfBuilder | None,
    classifier: PdfClassifier | None,
    ocr_image_coverage: float,
) -> tuple[ExtractionResult, bytes | None]:
    pages = pdf_extractor.extract_pages(path)
    coverage = classifier.page_image_coverage(path) if classifier is not None else []

    def _cov(i: int) -> float:
        return coverage[i] if i < len(coverage) else 0.0

    # A page needs OCR if it has no embedded text, or it is essentially a full-page image
    # (a scan) -- in which case any existing embedded text layer is dropped and re-OCR'd.
    needs_ocr = [
        i for i in range(len(pages)) if not pages[i].strip() or _cov(i) >= ocr_image_coverage
    ]

    if not needs_ocr:
        return ExtractionResult(_pdf_markdown(pages), pages, "pdf_text", len(pages)), None

    if ocr is None or renderer is None:
        if all(not text.strip() for text in pages):
            raise NeedsOcrError("PDF has no embedded text; OCR required")
        # No OCR available: keep whatever embedded text exists rather than failing.
        return ExtractionResult(_pdf_markdown(pages), pages, "pdf_text", len(pages)), None

    images = renderer.render_pages(path)
    confidences: list[float | None] = []
    for i in needs_ocr:
        if i < len(images):
            page = ocr.ocr_image(images[i])
            pages[i] = page.text  # drop any prior (weak) text layer for this page
            confidences.append(page.confidence)

    fully_ocr = len(needs_ocr) == len(pages)
    method = "ocr" if fully_ocr else "pdf_mixed"
    result = ExtractionResult(
        _pdf_markdown(pages), pages, method, len(pages), _average(confidences)
    )
    normalized = None
    if fully_ocr and builder is not None:
        normalized = builder.build(
            [RenderedPage(images[i], pages[i]) for i in range(min(len(images), len(pages)))]
        )
    return result, normalized
