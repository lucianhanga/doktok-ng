"""Born-digital extraction adapters (M2).

- ``DirectTextExtractor`` reads text/plain and text/markdown as-is (canonical content is the
  original text; brief section 13).
- ``PyMuPdfTextExtractor`` extracts embedded text from born-digital PDFs page by page. Scanned PDFs
  (no embedded text) yield empty pages and are routed to OCR in M3.
"""

from __future__ import annotations

from pathlib import Path


class DirectTextExtractor:
    """``TextExtractor`` for plain text and Markdown."""

    def extract(self, path: str) -> str:
        # Untrusted content: decode tolerantly rather than raising on odd bytes.
        return Path(path).read_text(encoding="utf-8", errors="replace")


class PyMuPdfTextExtractor:
    """``PdfTextExtractor`` returning per-page embedded text via PyMuPDF."""

    def extract_pages(self, path: str) -> list[str]:
        import fitz  # PyMuPDF; imported lazily so the package imports without the native lib

        pages: list[str] = []
        with fitz.open(path) as doc:
            for page in doc:
                pages.append(page.get_text("text"))
        return pages


class PyMuPdfClassifier:
    """``PdfClassifier`` reporting how much of each page is covered by its largest image.

    A near-1.0 value means the page is essentially a full-page image (a scan), regardless of any
    embedded text layer on top of it.
    """

    def page_image_coverage(self, path: str) -> list[float]:
        import fitz

        coverages: list[float] = []
        with fitz.open(path) as doc:
            for page in doc:
                page_area = abs(page.rect.width * page.rect.height) or 1.0
                largest = 0.0
                for info in page.get_image_info():
                    bbox = fitz.Rect(info["bbox"])
                    largest = max(largest, abs(bbox.width * bbox.height) / page_area)
                coverages.append(min(largest, 1.0))
        return coverages
