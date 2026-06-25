"""RapidOcr adapter: result mapping with an injected engine (no real ONNX runtime needed)."""

from __future__ import annotations

import io
from typing import Any

from doktok_provider_rapidocr.ocr import RapidOcr
from PIL import Image


class _FakeEngine:
    """Mimics RapidOCR's callable: returns (list of [box, text, score], elapse)."""

    def __call__(self, _img: Any) -> tuple[list[Any], float]:
        result = [
            [[[10, 10], [100, 10], [100, 30], [10, 30]], "Hello", 0.98],
            [[[10, 40], [120, 40], [120, 60], [10, 60]], "World", 0.95],
        ]
        return result, 0.1


def _png(w: int = 200, h: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_maps_rapidocr_result_to_page() -> None:
    page = RapidOcr(engine=_FakeEngine()).ocr_image(_png())
    assert page.text == "Hello\nWorld"  # reading order top-to-bottom
    assert page.confidence is not None and 0.9 < page.confidence < 1.0
    assert [ln.text for ln in page.lines] == ["Hello", "World"]
    assert page.lines[0].x0 == 10 and page.lines[0].y0 == 10
    assert page.width == 200 and page.height == 100


def test_empty_result_is_handled() -> None:
    class _Empty:
        def __call__(self, _img: Any) -> tuple[None, float]:
            return None, 0.0

    page = RapidOcr(engine=_Empty()).ocr_image(_png())
    assert page.text == "" and page.lines == [] and page.confidence is None
