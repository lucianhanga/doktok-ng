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


class PyMuPdfThumbnailer:
    """``Thumbnailer`` that renders a document's first page to a small WebP preview.

    Renders straight from the canonical (normalized) PDF at roughly the target size - so it is
    uniform for born-digital PDFs and OCR'd scans alike - then does a precise Lanczos downscale and
    WebP encode. fitz/Pillow are imported lazily so importing this class needs no native deps.
    """

    def thumbnail(self, source_path: str, *, max_edge: int = 400) -> bytes:
        import io

        import fitz
        from PIL import Image

        with fitz.open(source_path) as doc:
            if doc.page_count == 0:
                raise ValueError("document has no pages to render")
            page = doc[0]
            rect = page.rect
            longest = max(rect.width, rect.height) or 1.0
            # Render near the target size (cap upscaling of tiny pages), then exact-fit below.
            zoom = min(max_edge / longest, 4.0)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=80, method=6)
        return buffer.getvalue()


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
