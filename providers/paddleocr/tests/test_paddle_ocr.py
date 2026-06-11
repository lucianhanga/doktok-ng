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
    pytest.importorskip("PIL")  # image decode needs pillow/numpy (the optional `engine` extra)
    pytest.importorskip("numpy")

    class FakeEngine:
        def predict(self, image: object) -> list[dict[str, object]]:
            return [
                {"rec_texts": ["page text"], "rec_scores": [0.97], "rec_boxes": [[0, 0, 10, 10]]}
            ]

    # A tiny 1x1 PNG so PIL can decode it without paddle installed.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da6360000002000001e221bc330000000049454e44ae426082"
    )
    ocr = PaddleOcr(engine=FakeEngine())
    result = ocr.ocr_image(png)
    assert result.text == "page text"
    assert result.confidence is not None and abs(result.confidence - 0.97) < 1e-6
