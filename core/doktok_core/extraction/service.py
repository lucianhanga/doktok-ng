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

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from doktok_contracts.media import OcrPageResult, PageLayout, RenderedPage
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
    # Per-page OCR geometry (aligned with ``pages``; None where a page has no OCR boxes, e.g.
    # born-digital text or the Ollama OCR path). Persisted to content.json. Empty = no layout.
    page_layouts: list[PageLayout | None] = field(default_factory=list)


def _pdf_markdown(pages: list[str]) -> str:
    return "\n\n".join(f"## Page {i}\n\n{page.strip()}" for i, page in enumerate(pages, start=1))


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
    ocr_concurrency: int = 1,
    ocr_dpi: int = 200,
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
            ocr_concurrency,
            ocr_dpi,
        )

    if mime.startswith("image/"):
        if ocr is None:
            raise NeedsOcrError(f"{mime} requires OCR")
        image = Path(path).read_bytes()
        page = ocr.ocr_image(image)
        # dpi=None: a source image's boxes are in its own pixels, not a rendered DPI.
        layout = PageLayout(page.width, page.height, None, page.lines) if page.lines else None
        result = ExtractionResult(
            page.text, [page.text], "ocr", 1, page.confidence, page_layouts=[layout]
        )
        normalized = (
            builder.build([RenderedPage(image, page.text, page.lines, rotation=page.rotation)])
            if builder
            else None
        )
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
    ocr_concurrency: int = 1,
    ocr_dpi: int = 200,
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
    ocr_results: dict[int, OcrPageResult] = {}
    used_ocr = [False] * len(pages)

    # Pass 1: decide which pages need OCR (skipping born-digital + clean-embedded pages), without
    # doing the expensive OCR yet. pages[i] still holds the original embedded text here.
    def _needs_ocr(i: int, embedded: str) -> bool:
        if not embedded.strip():
            return True  # blank page -> must OCR
        if _cov(i) < ocr_image_coverage:
            return False  # born-digital text page
        # Scan-candidate with embedded text: OCR unless that text is already clearly clean.
        return not (ocr_min_text_quality > 0 and text_quality(embedded) >= ocr_min_text_quality)

    to_ocr = [i for i, embedded in enumerate(pages) if _needs_ocr(i, embedded)]

    if to_ocr:
        images = renderer.render_pages(path, dpi=ocr_dpi)

        # Pass 2: OCR the pages that need it - in parallel across the predictor pool (PaddleOCR is
        # CPU-bound and thread-safe per predictor), so a multi-page scan uses many cores at once.
        def _ocr_page(i: int) -> OcrPageResult:
            if images and i < len(images):
                return ocr.ocr_image(images[i])
            return OcrPageResult(text="")

        if ocr_concurrency > 1 and len(to_ocr) > 1:
            with ThreadPoolExecutor(max_workers=min(ocr_concurrency, len(to_ocr))) as pool:
                ocr_results = dict(zip(to_ocr, pool.map(_ocr_page, to_ocr), strict=True))
        else:
            ocr_results = {i: _ocr_page(i) for i in to_ocr}

        # Pass 3: assign the result (sequential; the embedded-vs-OCR decision may call the LLM).
        for i in to_ocr:
            embedded = pages[i]
            ocr_text = ocr_results[i].text
            if not embedded.strip():
                pages[i] = ocr_text  # nothing to compare against
                used_ocr[i] = True
            else:
                chosen, picked_ocr = choose_text(embedded, ocr_text, chat_model=chat_model)
                pages[i] = chosen
                used_ocr[i] = picked_ocr

    any_ocr = any(used_ocr)
    if not any_ocr:
        method = "pdf_text"
    elif all(used_ocr):
        method = "ocr"
    else:
        method = "pdf_mixed"

    # Per-page OCR geometry for content.json: only for pages whose OCR text was chosen and that
    # have line boxes (the boxes are in the rendered image's pixels at ocr_dpi).
    page_layouts: list[PageLayout | None] = []
    for i in range(len(pages)):
        r = ocr_results.get(i)
        if used_ocr[i] and r is not None and r.lines:
            page_layouts.append(PageLayout(r.width, r.height, ocr_dpi, r.lines))
        else:
            page_layouts.append(None)

    # PaddleOCR per-page confidence is not threaded back through this path, so the PDF-level
    # confidence stays unset (as before).
    result = ExtractionResult(
        _pdf_markdown(pages), pages, method, len(pages), None, page_layouts=page_layouts
    )
    normalized = None
    if all(used_ocr) and images is not None and builder is not None:
        # Every page chose OCR here, so attach each page's line boxes for a positioned text layer.
        normalized = builder.build(
            [
                RenderedPage(
                    images[i],
                    pages[i],
                    ocr_results[i].lines if i in ocr_results else [],
                    rotation=ocr_results[i].rotation if i in ocr_results else 0,
                )
                for i in range(min(len(images), len(pages)))
            ]
        )
    return result, normalized
