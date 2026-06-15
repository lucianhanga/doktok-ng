"""Real PyMuPDF renderer + searchable-PDF builder tests (no LLM required)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from doktok_contracts.media import OcrTextLine, RenderedPage
from doktok_modalities_files import (
    PyMuPdfClassifier,
    PyMuPdfRenderer,
    SearchablePdfBuilder,
    rotate_source,
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


def test_searchable_pdf_positions_text_at_line_boxes(tmp_path: Path) -> None:
    import fitz

    image = PyMuPdfRenderer().render_pages(_make_pdf(tmp_path, "x"), dpi=120)[0]
    line = OcrTextLine(text="positioned", x0=100, y0=200, x1=400, y1=240)
    pdf_bytes = SearchablePdfBuilder().build(
        [RenderedPage(image_png=image, text="ignored when lines are present", lines=[line])]
    )

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        # get_text("words") -> [x0, y0, x1, y1, word, ...]; the invisible layer still has positions.
        hits = [w for w in doc[0].get_text("words") if w[4] == "positioned"]
        assert hits, "expected the positioned line to be searchable"
        x0, y0, _x1, _y1 = hits[0][:4]
        # Sits at the line box (image px == PDF points), not whole-page flowed at the top-left.
        assert 90 <= x0 <= 410 and 190 <= y0 <= 260


def test_rotate_source_pdf_bumps_page_rotation(tmp_path: Path) -> None:
    import fitz

    data = Path(_make_pdf(tmp_path, "hello")).read_bytes()
    rotated = rotate_source(data, "application/pdf", 90)
    with fitz.open(stream=rotated, filetype="pdf") as doc:
        assert doc[0].rotation == 90  # lossless /Rotate bump
    assert rotate_source(data, "application/pdf", 0) == data  # no-op


def test_rotate_source_image_swaps_dimensions() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 10), "white").save(buffer, format="PNG")
    rotated = rotate_source(buffer.getvalue(), "image/png", 90)
    with Image.open(io.BytesIO(rotated)) as image:
        assert image.size == (10, 40)  # a 90deg turn swaps width/height


def test_rotate_source_rejects_unsupported_type() -> None:
    with pytest.raises(ValueError, match="cannot rotate"):
        rotate_source(b"x", "text/plain", 90)


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
