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
