"""Extraction resource guard: oversized PDFs are rejected before the render/OCR loop (review B5)."""

from __future__ import annotations

import pytest
from doktok_core.extraction.service import MIME_PDF, TooManyPagesError, extract_document


class _FakePdfExtractor:
    def __init__(self, pages: int) -> None:
        self._pages = pages

    def extract_pages(self, path: str) -> list[str]:  # noqa: ARG002
        return [f"page {i} text" for i in range(self._pages)]


class _FakeText:
    def extract(self, path: str) -> str:  # noqa: ARG002
        return ""


def test_pdf_over_max_pages_is_rejected() -> None:
    with pytest.raises(TooManyPagesError):
        extract_document(
            MIME_PDF,
            "/tmp/x.pdf",
            text_extractor=_FakeText(),
            pdf_extractor=_FakePdfExtractor(10),
            max_pages=5,
        )


def test_pdf_within_limit_is_extracted() -> None:
    result, _ = extract_document(
        MIME_PDF,
        "/tmp/x.pdf",
        text_extractor=_FakeText(),
        pdf_extractor=_FakePdfExtractor(3),
        max_pages=5,
    )
    assert result.page_count == 3
