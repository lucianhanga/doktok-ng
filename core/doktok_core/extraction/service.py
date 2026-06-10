"""Extraction routing (M2 + M3).

Maps a MIME type to extracted content, transparently falling back to OCR when there is no embedded
text and OCR services are available:

- text/plain, text/markdown        -> direct text
- born-digital PDF (has text)      -> embedded text (PyMuPDF)
- scanned PDF (no embedded text)   -> OCR every page (M3)
- mixed PDF (some blank pages)     -> embedded text kept; only blank pages OCR'd (M3)
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
) -> tuple[ExtractionResult, bytes | None]:
    if mime in (MIME_TEXT, MIME_MARKDOWN):
        text = text_extractor.extract(path)
        method = "markdown" if mime == MIME_MARKDOWN else "text"
        return ExtractionResult(text, [text], method, 1), None

    if mime == MIME_PDF:
        return _extract_pdf(path, pdf_extractor, ocr, renderer, builder)

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
) -> tuple[ExtractionResult, bytes | None]:
    pages = pdf_extractor.extract_pages(path)
    blanks = [i for i, text in enumerate(pages) if not text.strip()]

    if not blanks:
        return ExtractionResult(_pdf_markdown(pages), pages, "pdf_text", len(pages)), None

    if ocr is None or renderer is None:
        if len(blanks) == len(pages):
            raise NeedsOcrError("PDF has no embedded text; OCR required")
        # Mixed PDF without OCR: keep the embedded text, leave blank pages empty (do not fail).
        return ExtractionResult(_pdf_markdown(pages), pages, "pdf_text", len(pages)), None

    images = renderer.render_pages(path)
    confidences: list[float | None] = []
    for i in blanks:
        if i < len(images):
            page = ocr.ocr_image(images[i])
            pages[i] = page.text
            confidences.append(page.confidence)

    fully_ocr = len(blanks) == len(pages)
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
