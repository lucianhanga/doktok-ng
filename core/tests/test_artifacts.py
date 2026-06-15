from doktok_contracts.media import OcrTextLine, PageLayout
from doktok_core.documents.artifacts import _content_pages, extension_for
from doktok_core.extraction.service import ExtractionResult


def test_extension_from_detected_mime() -> None:
    assert extension_for("application/pdf", "anything") == ".pdf"
    assert extension_for("text/plain", "weird.name") == ".txt"
    assert extension_for("text/markdown", "notes") == ".md"
    assert extension_for("image/png", "x") == ".png"


def test_extension_falls_back_to_filename_when_mime_unknown() -> None:
    assert extension_for("application/x-unknown-xyz", "report.PDF") == ".PDF"
    assert extension_for(None, "data.csv") == ".csv"
    assert extension_for(None, "noext") == ""


def test_content_pages_persists_ocr_geometry() -> None:
    layout = PageLayout(
        width_px=1654, height_px=2339, dpi=200, lines=[OcrTextLine("Hello", 10, 20, 90, 40)]
    )
    result = ExtractionResult(
        content_md="Hello\n\nplain",
        pages=["Hello", "plain"],
        extraction_method="pdf_mixed",
        page_count=2,
        page_layouts=[layout, None],  # page 1 OCR'd; page 2 born-digital (no geometry)
    )
    pages = _content_pages(result)

    assert pages[0]["page_number"] == 1 and pages[0]["text"] == "Hello"
    assert pages[0]["render_dpi"] == 200
    assert (pages[0]["width_px"], pages[0]["height_px"]) == (1654, 2339)
    assert pages[0]["lines"] == [{"text": "Hello", "bbox": [10, 20, 90, 40]}]
    # A page without OCR geometry stays text-only (no bbox keys).
    assert pages[1] == {"page_number": 2, "text": "plain"}


def test_content_pages_handles_no_layouts() -> None:
    result = ExtractionResult("t", ["t"], "text", 1)  # page_layouts defaults to empty
    assert _content_pages(result) == [{"page_number": 1, "text": "t"}]


def test_extraction_result_strips_nul_bytes() -> None:
    # NUL (0x00) bytes from some OCR'd PDFs are stripped at the source so chunk/entity indexing
    # does not fail with "PostgreSQL text fields cannot contain NUL".
    result = ExtractionResult("a\x00b", ["pg1\x00", "clean"], "ocr", 2)
    assert result.content_md == "ab"
    assert result.pages == ["pg1", "clean"]
