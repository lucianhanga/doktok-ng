"""PDF page rendering and searchable-PDF assembly (PyMuPDF), used by the OCR path (M3)."""

from __future__ import annotations

from doktok_contracts.media import RenderedPage


def rotate_source(data: bytes, mime: str | None, degrees: int) -> bytes:
    """Rotate a document clockwise by 90/180/270 degrees and return the new bytes.

    PDFs get a lossless ``/Rotate`` bump (no re-rasterization), which both renders upright for OCR
    and displays upright in a viewer; images are re-encoded rotated. Raises ``ValueError`` for an
    unsupported type or a non-multiple-of-90 angle.
    """
    degrees %= 360
    if degrees == 0:
        return data
    if degrees not in (90, 180, 270):
        raise ValueError("degrees must be a multiple of 90")

    if mime == "application/pdf":
        import fitz

        doc = fitz.open(stream=data, filetype="pdf")
        try:
            for page in doc:
                page.set_rotation((page.rotation + degrees) % 360)
            return bytes(doc.tobytes())
        finally:
            doc.close()

    if mime and mime.startswith("image/"):
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format or "PNG"
            # PIL rotates counter-clockwise; negate for clockwise. expand keeps the full content.
            rotated = image.rotate(-degrees, expand=True)
            buffer = io.BytesIO()
            rotated.save(buffer, format=fmt)
            return buffer.getvalue()

    raise ValueError(f"cannot rotate {mime}")


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
            # The Enhanced 4-way vote returns boxes in a rotated frame; rotate the image to match so
            # it is stored upright and the boxes still line up.
            image_png = (
                rotate_source(page.image_png, "image/png", page.rotation)
                if page.rotation
                else page.image_png
            )
            image = fitz.open(stream=image_png, filetype="png")
            rect = image[0].rect
            image.close()
            new_page = out.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(rect, stream=image_png)
            # render_mode=3 -> invisible text: searchable/selectable, not drawn over the image.
            if page.lines:
                # Positioned layer: each line sits over the words it came from. The page is sized to
                # the image's pixels, so the OCR boxes (image px) are already PDF points (DPI-safe).
                # Use point-based insert_text (not insert_textbox, which rejects when the font is
                # taller than the box): baseline near the box bottom, font fit to the box height.
                for line in page.lines:
                    if not line.text.strip():
                        continue
                    height = line.y1 - line.y0
                    fontsize = max(1.0, height * 0.8)
                    new_page.insert_text(
                        (line.x0, line.y1 - height * 0.15),
                        line.text,
                        fontsize=fontsize,
                        render_mode=3,
                    )
            elif page.text.strip():
                # Fallback (no per-line boxes, e.g. the Ollama vision OCR path): whole-page flow.
                new_page.insert_textbox(rect, page.text, fontsize=8, render_mode=3)
        data: bytes = out.tobytes()
        out.close()
        return data
