"""RapidOcr adapter: result mapping with an injected engine (no real ONNX runtime needed)."""

from __future__ import annotations

import io
from typing import Any

import pytest
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

        def result(self, timeout: float | None = None) -> Any:
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


class _Sentinel:
    pass


def test_pool_uses_spawn_context_and_page_timeout(monkeypatch) -> None:
    """The pool must spawn (fork inside the multi-threaded worker deadlocks children) and every
    page call must carry a timeout so a wedged child can never hang the worker (2026-10-26)."""
    import doktok_provider_rapidocr.ocr as mod

    captured: dict[str, Any] = {}
    sentinel = _Sentinel()

    class _FakeFuture:
        def result(self, timeout: float | None = None) -> Any:
            captured["timeout"] = timeout
            return sentinel

    class _FakePool:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        def submit(self, fn: Any, payload: bytes) -> _FakeFuture:
            return _FakeFuture()

        def shutdown(self, wait: bool = True) -> None:
            pass

    monkeypatch.setattr(mod, "ProcessPoolExecutor", _FakePool)
    ocr = RapidOcr(pool_size=2)
    assert ocr.ocr_image(_png()) is sentinel
    assert captured["kwargs"]["mp_context"].get_start_method() == "spawn"
    assert captured["timeout"] == mod._PAGE_TIMEOUT_S


def test_wedged_child_discards_pool_and_propagates(monkeypatch) -> None:
    import doktok_provider_rapidocr.ocr as mod

    class _WedgedFuture:
        def result(self, timeout: float | None = None) -> Any:
            raise TimeoutError

    class _FakePool:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def submit(self, fn: Any, payload: bytes) -> _WedgedFuture:
            return _WedgedFuture()

        def shutdown(self, wait: bool = True) -> None:
            pass

    monkeypatch.setattr(mod, "ProcessPoolExecutor", _FakePool)
    ocr = RapidOcr(pool_size=1)
    with pytest.raises(TimeoutError):
        ocr.ocr_image(_png())
    assert ocr._pool is None  # wedged pool dropped so the next call rebuilds fresh
