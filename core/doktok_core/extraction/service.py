"""Extraction routing (M2): map a MIME type to the right extractor and produce canonical content.

Born-digital only in M2: text/plain, text/markdown, and PDFs with embedded text. Images and scanned
PDFs (no embedded text) raise ``NeedsOcrError`` and are handled by OCR in M3.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from doktok_contracts.ports import PdfTextExtractor, TextExtractor

MIME_TEXT = "text/plain"
MIME_MARKDOWN = "text/markdown"
MIME_PDF = "application/pdf"


class NeedsOcrError(Exception):
    """Raised when a file has no extractable embedded text and requires OCR (M3)."""


@dataclass
class ExtractionResult:
    content_md: str
    pages: list[str]
    extraction_method: str
    page_count: int
    metadata: dict[str, str] = field(default_factory=dict)


def extract(
    mime: str,
    path: str,
    *,
    text_extractor: TextExtractor,
    pdf_extractor: PdfTextExtractor,
) -> ExtractionResult:
    if mime in (MIME_TEXT, MIME_MARKDOWN):
        text = text_extractor.extract(path)
        method = "markdown" if mime == MIME_MARKDOWN else "text"
        return ExtractionResult(
            content_md=text, pages=[text], extraction_method=method, page_count=1
        )

    if mime == MIME_PDF:
        pages = pdf_extractor.extract_pages(path)
        if not any(page.strip() for page in pages):
            raise NeedsOcrError("PDF has no embedded text; OCR required (M3)")
        content_md = "\n\n".join(
            f"## Page {i}\n\n{page.strip()}" for i, page in enumerate(pages, start=1)
        )
        return ExtractionResult(
            content_md=content_md,
            pages=pages,
            extraction_method="pdf_text",
            page_count=len(pages),
        )

    raise NeedsOcrError(f"no born-digital extractor for {mime}; OCR required (M3)")
