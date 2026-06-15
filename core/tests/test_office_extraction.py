"""Office documents (docx/xlsx/pptx) are normalized to PDF, then run the PDF path (#313)."""

from __future__ import annotations

import pytest
from doktok_core.extraction.service import (
    MIME_DOCX,
    MIME_PPTX,
    MIME_XLSX,
    NeedsOcrError,
    extract_document,
)


class _FakePdfExtractor:
    def extract_pages(self, path: str) -> list[str]:  # noqa: ARG002
        return ["Hello from the office document"]


class _FakeText:
    def extract(self, path: str) -> str:  # noqa: ARG002
        return ""


class _FakeNormalizer:
    """Records the conversion call and returns placeholder PDF bytes (PDF extractor is faked)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def to_pdf(self, path: str, mime: str) -> bytes:
        self.calls.append((path, mime))
        return b"%PDF-1.4 fake"


@pytest.mark.parametrize("mime", [MIME_DOCX, MIME_XLSX, MIME_PPTX])
def test_office_doc_routes_through_normalizer_to_pdf_path(mime: str) -> None:
    normalizer = _FakeNormalizer()
    result, _ = extract_document(
        mime,
        "/tmp/doc.office",
        text_extractor=_FakeText(),
        pdf_extractor=_FakePdfExtractor(),
        normalizer=normalizer,
    )

    assert normalizer.calls == [("/tmp/doc.office", mime)]
    assert result.extraction_method == "pdf_text"
    assert "Hello from the office document" in result.content_md


def test_office_doc_without_normalizer_raises() -> None:
    with pytest.raises(NeedsOcrError):
        extract_document(
            MIME_DOCX,
            "/tmp/doc.docx",
            text_extractor=_FakeText(),
            pdf_extractor=_FakePdfExtractor(),
        )
