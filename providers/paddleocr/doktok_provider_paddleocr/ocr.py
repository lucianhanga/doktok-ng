"""OCR via PaddleOCR (PP-OCRv5/v6), a drop-in ``OcrExtractor`` (ADR-0003).

PaddleOCR is a detection+recognition pipeline (DBNet + CTC), NOT a generative VLM, so it cannot
fall into the repeat-loops that produce garbage on sparse/stamp pages - it simply returns no text.
The output is kept shape-compatible with the previous OCR adapter: a single ``OcrPageResult`` with
the page text (lines joined in reading order) and a confidence (PaddleOCR's mean per-line score), so
nothing downstream in the pipeline changes.

The heavy ``paddleocr``/``paddlepaddle`` runtime is imported lazily and the engine is loaded once;
calls are serialized with a lock (paddle inference is not guaranteed thread-safe).
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any

from doktok_contracts.media import OcrPageResult

logger = logging.getLogger("doktok.ocr.paddle")


class PaddleOcr:
    """``OcrExtractor`` backed by PaddleOCR."""

    def __init__(
        self,
        *,
        lang: str = "german",
        det_model: str = "PP-OCRv5_mobile_det",
        rec_model: str = "latin_PP-OCRv5_mobile_rec",
        engine: Any = None,
    ) -> None:
        # ``lang='german'`` selects the Latin recognizer (German/English/most European scripts). The
        # mobile det+rec models are ~40% faster than the medium defaults at ~0.98 confidence.
        self._lang = lang
        self._det_model = det_model
        self._rec_model = rec_model
        self._engine = engine
        self._lock = threading.Lock()

    def _get_engine(self) -> Any:
        if self._engine is None:
            from paddleocr import PaddleOCR

            logger.info("loading PaddleOCR (lang=%s); first run downloads the models", self._lang)
            # Skip the heavy document-orientation + unwarping preprocessing models: rendered PDF
            # pages are already upright and flat, and those models roughly triple per-page latency.
            self._engine = PaddleOCR(
                lang=self._lang,
                text_detection_model_name=self._det_model,
                text_recognition_model_name=self._rec_model,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._engine

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        import numpy as np
        from PIL import Image

        rgb = np.array(Image.open(io.BytesIO(image_png)).convert("RGB"))
        bgr = rgb[:, :, ::-1]  # PaddleOCR expects OpenCV (BGR) order
        with self._lock:
            results = self._get_engine().predict(bgr)

        texts: list[str] = []
        scores: list[float] = []
        for item in results or []:
            page_text, page_scores = assemble_text(item)
            if page_text:
                texts.append(page_text)
            scores.extend(page_scores)
        confidence = sum(scores) / len(scores) if scores else None
        return OcrPageResult(text="\n".join(texts), confidence=confidence)


def _as_list(value: Any) -> list[Any]:
    return [] if value is None else list(value)


def assemble_text(result: Any) -> tuple[str, list[float]]:
    """Join a PaddleOCR result's recognized lines in reading order (top-to-bottom, left-to-right).

    ``result`` is PaddleOCR's per-image result (a dict with ``rec_texts``/``rec_scores`` and either
    ``rec_boxes`` [x1,y1,x2,y2] or ``rec_polys``/``dt_polys`` [[x,y]x4]).
    """
    texts = _as_list(result.get("rec_texts"))
    scores = [float(s) for s in _as_list(result.get("rec_scores"))]
    boxes = result.get("rec_boxes")
    polys = result.get("rec_polys")
    if polys is None:
        polys = result.get("dt_polys")

    def top_left(i: int) -> tuple[float, float]:
        if boxes is not None and i < len(boxes):
            box = boxes[i]
            return (float(box[1]), float(box[0]))  # (y1, x1)
        if polys is not None and i < len(polys):
            pts = polys[i]
            return (min(float(p[1]) for p in pts), min(float(p[0]) for p in pts))
        return (float(i), 0.0)

    # Bucket the y-coordinate so lines on the same row sort left-to-right.
    order = sorted(range(len(texts)), key=lambda i: (round(top_left(i)[0] / 10.0), top_left(i)[1]))
    text = "\n".join(str(texts[i]) for i in order)
    ordered_scores = [scores[i] for i in order if i < len(scores)]
    return text, ordered_scores
