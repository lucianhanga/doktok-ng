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


def test_broken_pool_is_rebuilt_and_page_retried(monkeypatch: Any) -> None:
    # Same failure mode as the PaddleOCR adapter: a dead pool child (OOM kill) breaks the whole
    # ProcessPoolExecutor; ocr_image must rebuild it and retry the page once.
    from concurrent.futures.process import BrokenProcessPool

    import doktok_provider_rapidocr.ocr as ocr_mod

    class _Future:
        def __init__(self, value: Any) -> None:
            self._value = value

        def result(self) -> Any:
            return self._value

    class _BrokenPool:
        def submit(self, fn: Any, *args: Any) -> _Future:
            raise BrokenProcessPool("A child process terminated abruptly")

        def shutdown(self, wait: bool = True) -> None: ...

    class _WorkingPool:
        def submit(self, fn: Any, *args: Any) -> _Future:
            return _Future(fn(*args))

        def shutdown(self, wait: bool = True) -> None: ...

    pool_kinds = iter([_BrokenPool, _WorkingPool])
    builds: list[Any] = []

    def _factory(**kwargs: Any) -> Any:
        pool = next(pool_kinds)()
        builds.append(pool)
        return pool

    monkeypatch.setattr(ocr_mod, "_WORKER_ENGINE", _FakeEngine())
    monkeypatch.setattr(ocr_mod, "ProcessPoolExecutor", _factory)

    ocr = RapidOcr(pool_size=2)
    assert ocr.ocr_image(_png()).text == "Hello\nWorld"
    assert len(builds) == 2  # the broken pool was replaced, not reused
    assert ocr.ocr_image(_png()).text == "Hello\nWorld"
    assert len(builds) == 2
