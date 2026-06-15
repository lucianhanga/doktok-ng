"""Tests for PaddleOCR result assembly (no paddle/paddlepaddle needed)."""

from __future__ import annotations

import pytest
from doktok_provider_paddleocr.ocr import PaddleOcr, assemble_lines, assemble_text


def test_assembles_lines_in_reading_order() -> None:
    # Lines given out of order; boxes are [x1, y1, x2, y2].
    result = {
        "rec_texts": ["world", "Hello", "second line"],
        "rec_scores": [0.9, 0.99, 0.95],
        "rec_boxes": [
            [120, 10, 200, 30],  # "world"  (row 0, right)
            [10, 12, 90, 30],  # "Hello"  (row 0, left)
            [10, 60, 200, 80],  # "second line" (row 1)
        ],
    }
    text, scores = assemble_text(result)
    assert text == "Hello\nworld\nsecond line"
    assert scores == [0.99, 0.9, 0.95]


def test_assemble_lines_returns_boxes_in_reading_order() -> None:
    # Same fixture as the ordering test; assemble_lines pairs each line with its bbox for the
    # positioned searchable-PDF text layer.
    result = {
        "rec_texts": ["world", "Hello"],
        "rec_scores": [0.9, 0.99],
        "rec_boxes": [[120, 10, 200, 30], [10, 12, 90, 30]],
    }
    lines = assemble_lines(result)
    assert [t for t, _ in lines] == ["Hello", "world"]  # reading order
    assert lines[0][1] == (10.0, 12.0, 90.0, 30.0)  # "Hello" box (x0,y0,x1,y1)
    # Lines without a usable box are dropped (the layer needs coordinates).
    assert assemble_lines({"rec_texts": ["x"], "rec_scores": [0.5]}) == []


def test_handles_polys_and_empty() -> None:
    poly_result = {
        "rec_texts": ["B", "A"],
        "rec_scores": [0.8, 0.85],
        "rec_polys": [
            [[10, 100], [50, 100], [50, 120], [10, 120]],  # "B" lower
            [[10, 10], [50, 10], [50, 30], [10, 30]],  # "A" upper
        ],
    }
    text, _ = assemble_text(poly_result)
    assert text == "A\nB"
    assert assemble_text({"rec_texts": [], "rec_scores": []}) == ("", [])


def test_ocr_image_uses_injected_engine() -> None:
    import io

    image_mod = pytest.importorskip("PIL.Image")  # decode needs pillow + numpy (the `engine` extra)
    pytest.importorskip("numpy")

    class FakeEngine:
        def predict(self, image: object) -> list[dict[str, object]]:
            return [
                {"rec_texts": ["page text"], "rec_scores": [0.97], "rec_boxes": [[0, 0, 10, 10]]}
            ]

    # A small valid PNG that PIL can decode without paddle installed.
    buffer = io.BytesIO()
    image_mod.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    png = buffer.getvalue()
    ocr = PaddleOcr(engine=FakeEngine())
    result = ocr.ocr_image(png)
    assert result.text == "page text"
    assert result.confidence is not None and abs(result.confidence - 0.97) < 1e-6


def test_run_ocr_assembles_from_engine() -> None:
    import io

    image_mod = pytest.importorskip("PIL.Image")
    pytest.importorskip("numpy")
    from doktok_provider_paddleocr.ocr import _run_ocr

    class FakeEngine:
        def predict(self, image: object) -> list[dict[str, object]]:
            return [{"rec_texts": ["hi"], "rec_scores": [0.8], "rec_boxes": [[0, 0, 5, 5]]}]

    buffer = io.BytesIO()
    image_mod.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    text, confidence, lines = _run_ocr(FakeEngine(), buffer.getvalue())
    assert text == "hi" and confidence is not None and abs(confidence - 0.8) < 1e-6
    # The per-line box is carried through (image pixels) for the positioned text layer.
    assert len(lines) == 1
    assert (lines[0].text, lines[0].x0, lines[0].y0, lines[0].x1, lines[0].y1) == ("hi", 0, 0, 5, 5)


def test_no_engine_dispatches_to_worker_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without an injected engine, ocr_image submits to the process pool. Here we stub the executor
    # with an inline one and a fake worker engine, so no real subprocess/paddle is needed.
    import doktok_provider_paddleocr.ocr as ocr_mod

    class _Future:
        def __init__(self, value: object) -> None:
            self._value = value

        def result(self) -> object:
            return self._value

    class _InlineExecutor:
        def submit(self, fn: object, *args: object) -> _Future:
            return _Future(fn(*args))  # type: ignore[operator]

    class FakeEngine:
        def predict(self, image: object) -> list[dict[str, object]]:
            return [{"rec_texts": ["X"], "rec_scores": [0.9], "rec_boxes": [[0, 0, 5, 5]]}]

    pytest.importorskip("PIL.Image")
    pytest.importorskip("numpy")
    import io

    monkeypatch.setattr(ocr_mod, "_WORKER_ENGINE", FakeEngine())
    monkeypatch.setattr(PaddleOcr, "_executor", lambda self: _InlineExecutor())

    buffer = io.BytesIO()
    __import__("PIL.Image")
    from PIL import Image

    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    result = PaddleOcr(pool_size=4).ocr_image(buffer.getvalue())
    assert result.text == "X" and result.confidence is not None


def test_reconfigure_resizes_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import doktok_provider_paddleocr.ocr as ocr_mod

    shutdowns: list[bool] = []

    class _FakePool:
        def __init__(self, **kwargs: object) -> None: ...

        def shutdown(self, wait: bool) -> None:
            shutdowns.append(wait)

    monkeypatch.setattr(ocr_mod, "ProcessPoolExecutor", _FakePool)
    ocr = PaddleOcr(pool_size=4)
    ocr._executor()  # build the pool at size 4
    ocr.reconfigure(8)  # resize -> the old pool is shut down, next use rebuilds at 8

    assert shutdowns == [True]
    assert ocr._pool is None and ocr._pool_size == 8
    ocr.reconfigure(8)  # same size -> no-op
    assert shutdowns == [True]


def test_reconfigure_with_injected_engine_is_noop() -> None:
    ocr = PaddleOcr(engine="fake-engine")
    ocr.reconfigure(8)
    assert ocr._pool_size == 8  # size recorded, but there is no pool to tear down


def test_executor_built_lazily_with_pool_size(monkeypatch: pytest.MonkeyPatch) -> None:
    import doktok_provider_paddleocr.ocr as ocr_mod

    captured: dict[str, object] = {}

    class _FakePool:
        def __init__(
            self, *, max_workers: int, initializer: object, initargs: tuple[object, ...]
        ) -> None:
            captured["max_workers"] = max_workers
            captured["initargs"] = initargs

    monkeypatch.setattr(ocr_mod, "ProcessPoolExecutor", _FakePool)
    ocr = PaddleOcr(pool_size=6)
    first = ocr._executor()
    assert ocr._executor() is first  # built once, then reused
    assert captured["max_workers"] == 6
    assert captured["initargs"] == ("german", "PP-OCRv5_mobile_det", "latin_PP-OCRv5_mobile_rec", 1)


def test_shutdown_tears_down_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import doktok_provider_paddleocr.ocr as ocr_mod

    calls: dict[str, object] = {}

    class _FakePool:
        def __init__(self, **_kwargs: object) -> None: ...

        def shutdown(self, wait: bool) -> None:
            calls["wait"] = wait

    monkeypatch.setattr(ocr_mod, "ProcessPoolExecutor", _FakePool)
    ocr = PaddleOcr(pool_size=2)
    ocr._executor()  # start the pool
    ocr.shutdown()
    assert calls["wait"] is True  # joined so spawn workers exit instead of orphaning
    assert ocr._pool is None
    ocr.shutdown()  # idempotent: safe with no pool running
