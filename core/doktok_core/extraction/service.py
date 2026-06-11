"""Extraction routing (M2 + M3).

Maps a MIME type to extracted content, falling back to OCR when needed:

- text/plain, text/markdown        -> direct text
- born-digital PDF (text pages)    -> embedded text (PyMuPDF)
- PDF page that is a full-page image (>= coverage threshold) with an embedded text layer ->
  if the embedded text is already clearly clean (quality >= ocr_min_text_quality) keep it; otherwise
  OCR the page and let the LLM judge (or a heuristic) decide whether the embedded text or the OCR
  text is better, keeping the winner
- PDF page with no embedded text   -> OCR
- image                            -> OCR (M3)

Returns the extraction result and an optional ``normalized/searchable.pdf`` (images + OCR text
layer), produced for fully OCR'd input. If OCR is needed but unavailable, raises ``NeedsOcrError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from doktok_contracts.media import RenderedPage
from doktok_contracts.ports import (
    ChatModelProvider,
    OcrExtractor,
    PdfClassifier,
    PdfRenderer,
    PdfTextExtractor,
    SearchablePdfBuilder,
    TextExtractor,
)

from doktok_core.extraction.judge import choose_text
from doktok_core.extraction.quality import text_quality

MIME_TEXT = "text/plain"
MIME_MARKDOWN = "text/markdown"
MIME_PDF = "application/pdf"


class NeedsOcrError(Exception):
    """Raised when a file needs OCR but no OCR services are configured."""


class TooManyPagesError(Exception):
    """Raised when a document exceeds the configured page-count limit (resource guard)."""


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
    ocr_min_text_quality: float = 0.0,
    chat_model: ChatModelProvider | None = None,
    max_pages: int = 0,
) -> tuple[ExtractionResult, bytes | None]:
    if mime in (MIME_TEXT, MIME_MARKDOWN):
        text = text_extractor.extract(path)
        method = "markdown" if mime == MIME_MARKDOWN else "text"
        return ExtractionResult(text, [text], method, 1), None

    if mime == MIME_PDF:
        return _extract_pdf(
            path,
            pdf_extractor,
            ocr,
            renderer,
            builder,
            classifier,
            ocr_image_coverage,
            ocr_min_text_quality,
            chat_model,
            max_pages,
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
    ocr_min_text_quality: float,
    chat_model: ChatModelProvider | None,
    max_pages: int = 0,
) -> tuple[ExtractionResult, bytes | None]:
    pages = pdf_extractor.extract_pages(path)
    # Resource guard: reject oversized PDFs before the expensive per-page render/OCR loop.
    if max_pages and len(pages) > max_pages:
        raise TooManyPagesError(f"PDF has {len(pages)} pages (limit {max_pages})")
    coverage = classifier.page_image_coverage(path) if classifier is not None else []

    def _cov(i: int) -> float:
        return coverage[i] if i < len(coverage) else 0.0

    # Without OCR services we can only use embedded text (M2 behaviour).
    if ocr is None or renderer is None:
        if all(not text.strip() for text in pages):
            raise NeedsOcrError("PDF has no embedded text; OCR required")
        return ExtractionResult(_pdf_markdown(pages), pages, "pdf_text", len(pages)), None

    images: list[bytes] | None = None
    used_ocr = [False] * len(pages)
    confidences: list[float | None] = []

    for i, embedded in enumerate(pages):
        image_page = _cov(i) >= ocr_image_coverage
        has_text = bool(embedded.strip())

        # Born-digital text page: keep embedded text, no OCR.
        if has_text and not image_page:
            continue
        # Full-page image whose embedded text is already clearly clean: trust it (fast path).
        if has_text and ocr_min_text_quality > 0 and text_quality(embedded) >= ocr_min_text_quality:
            continue

        if images is None:
            images = renderer.render_pages(path)
        ocr_text = ocr.ocr_image(images[i]).text if i < len(images) else ""

        if not has_text:
            pages[i] = ocr_text  # nothing to compare against
            used_ocr[i] = True
        else:
            # Ambiguous: let the LLM (or heuristic) decide embedded vs OCR.
            chosen, picked_ocr = choose_text(embedded, ocr_text, chat_model=chat_model)
            pages[i] = chosen
            used_ocr[i] = picked_ocr
        if used_ocr[i]:
            confidences.append(None)

    any_ocr = any(used_ocr)
    if not any_ocr:
        method = "pdf_text"
    elif all(used_ocr):
        method = "ocr"
    else:
        method = "pdf_mixed"

    result = ExtractionResult(
        _pdf_markdown(pages), pages, method, len(pages), _average(confidences)
    )
    normalized = None
    if all(used_ocr) and images is not None and builder is not None:
        normalized = builder.build(
            [RenderedPage(images[i], pages[i]) for i in range(min(len(images), len(pages)))]
        )
    return result, normalized
