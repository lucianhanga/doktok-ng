"""Real PyMuPDF renderer + searchable-PDF builder tests (no LLM required)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.media import RenderedPage
from doktok_modalities_files import PyMuPdfRenderer, SearchablePdfBuilder


def _make_pdf(tmp_path: Path, text: str) -> str:
    import fitz

    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_renderer_produces_png_pages(tmp_path: Path) -> None:
    images = PyMuPdfRenderer().render_pages(_make_pdf(tmp_path, "hello"), dpi=120)
    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")  # PNG signature


def test_searchable_pdf_contains_text_layer(tmp_path: Path) -> None:
    import fitz

    image = PyMuPdfRenderer().render_pages(_make_pdf(tmp_path, "anything"), dpi=120)[0]
    pdf_bytes = SearchablePdfBuilder().build(
        [RenderedPage(image_png=image, text="searchable words")]
    )

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert "searchable words" in doc[0].get_text()
