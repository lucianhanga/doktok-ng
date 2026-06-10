"""Real PyMuPDF renderer + searchable-PDF builder tests (no LLM required)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.media import RenderedPage
from doktok_modalities_files import (
    PyMuPdfClassifier,
    PyMuPdfRenderer,
    SearchablePdfBuilder,
)


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


def test_classifier_flags_full_page_image_and_ignores_plain_text(tmp_path: Path) -> None:
    # A full-page image page (image + text layer) -> high coverage; a text-only page -> ~0.
    image = PyMuPdfRenderer().render_pages(_make_pdf(tmp_path, "scanned"), dpi=120)[0]
    scanned_pdf = SearchablePdfBuilder().build([RenderedPage(image_png=image, text="layer")])
    scanned_path = tmp_path / "scanned.pdf"
    scanned_path.write_bytes(scanned_pdf)

    coverage = PyMuPdfClassifier().page_image_coverage(str(scanned_path))
    assert coverage[0] >= 0.8

    text_only = PyMuPdfClassifier().page_image_coverage(_make_pdf(tmp_path, "just text"))
    assert text_only[0] < 0.5
