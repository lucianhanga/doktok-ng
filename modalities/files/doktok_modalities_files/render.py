"""PDF page rendering and searchable-PDF assembly (PyMuPDF), used by the OCR path (M3)."""

from __future__ import annotations

from doktok_contracts.media import RenderedPage


class PyMuPdfRenderer:
    """``PdfRenderer`` that rasterizes PDF pages to PNG bytes."""

    def render_pages(self, path: str, dpi: int = 200) -> list[bytes]:
        import fitz

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        images: list[bytes] = []
        with fitz.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(matrix=matrix)
                images.append(pix.tobytes("png"))
        return images


class SearchablePdfBuilder:
    """``SearchablePdfBuilder`` assembling a PDF of page images + an invisible OCR text layer."""

    def build(self, pages: list[RenderedPage]) -> bytes:
        import fitz

        out = fitz.open()
        for page in pages:
            image = fitz.open(stream=page.image_png, filetype="png")
            rect = image[0].rect
            image.close()
            new_page = out.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(rect, stream=page.image_png)
            if page.text.strip():
                # render_mode=3 -> invisible text: searchable/selectable, not drawn over the image.
                new_page.insert_textbox(rect, page.text, fontsize=8, render_mode=3)
        data: bytes = out.tobytes()
        out.close()
        return data
