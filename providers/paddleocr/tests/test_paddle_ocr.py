"""Tests for PaddleOCR result assembly (no paddle/paddlepaddle needed)."""

from __future__ import annotations

import pytest
from doktok_provider_paddleocr.ocr import PaddleOcr, assemble_text


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


def test_engine_pool_grows_to_pool_size_then_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    # Up to pool_size independent predictors are built for concurrent pages, then reused - no
    # global lock serializing all OCR. (Uses fake predictors so no paddle/numpy is needed.)
    import itertools

    built = itertools.count()
    monkeypatch.setattr(PaddleOcr, "_build_engine", lambda self: f"engine-{next(built)}")

    ocr = PaddleOcr(pool_size=2)
    e1 = ocr._acquire()
    e2 = ocr._acquire()  # first is still in flight -> a second predictor is built
    assert {e1, e2} == {"engine-0", "engine-1"}

    ocr._pool.put(e1)  # page finished
    assert ocr._acquire() == e1  # idle predictor reused
    assert ocr._created == 2  # never exceeds pool_size


def test_injected_engine_is_the_only_predictor() -> None:
    ocr = PaddleOcr(engine="fake-engine")
    assert ocr._acquire() == "fake-engine"
    ocr._pool.put("fake-engine")
    assert ocr._acquire() == "fake-engine"  # pinned to the one injected predictor
