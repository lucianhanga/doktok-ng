"""OCR via PaddleOCR (PP-OCRv5/v6), a drop-in ``OcrExtractor`` (ADR-0003).

PaddleOCR is a detection+recognition pipeline (DBNet + CTC), NOT a generative VLM, so it cannot
fall into the repeat-loops that produce garbage on sparse/stamp pages - it simply returns no text.
The output is kept shape-compatible with the previous OCR adapter: a single ``OcrPageResult`` with
the page text (lines joined in reading order) and a confidence (PaddleOCR's mean per-line score), so
nothing downstream in the pipeline changes.

The heavy ``paddleocr``/``paddlepaddle`` runtime is imported lazily. A single predictor is not
thread-safe, so instead of serializing all OCR behind one lock we keep a small pool of independent
predictors (``pool_size``) and run them concurrently - one per page in flight.
"""

from __future__ import annotations

import io
import logging
import queue
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
        pool_size: int = 1,
    ) -> None:
        # ``lang='german'`` selects the Latin recognizer (German/English/most European scripts). The
        # mobile det+rec models are ~40% faster than the medium defaults at ~0.98 confidence.
        self._lang = lang
        self._det_model = det_model
        self._rec_model = rec_model
        # A pool of independent PaddleOCR predictors so up to ``pool_size`` pages OCR concurrently.
        # A single predictor is not thread-safe (hence the previous global lock), but separate
        # predictors are - so we keep one per concurrent slot instead of serializing all OCR.
        self._pool: queue.Queue[Any] = queue.Queue()
        self._created = 0
        self._build_lock = threading.Lock()  # serialize the (heavy) model load, not inference
        if engine is not None:
            self._pool.put(engine)  # injected predictor (tests): the pool holds exactly this one
            self._created = 1
            self._pool_size = 1
        else:
            self._pool_size = max(1, pool_size)

    def _build_engine(self) -> Any:
        from paddleocr import PaddleOCR

        logger.info("loading PaddleOCR (lang=%s); first run downloads the models", self._lang)
        # Skip the heavy document-orientation + unwarping preprocessing models: rendered PDF pages
        # are already upright and flat, and those models roughly triple per-page latency.
        return PaddleOCR(
            lang=self._lang,
            text_detection_model_name=self._det_model,
            text_recognition_model_name=self._rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def _acquire(self) -> Any:
        try:
            return self._pool.get_nowait()  # an idle predictor is available
        except queue.Empty:
            pass
        with self._build_lock:
            if self._created < self._pool_size:
                engine = self._build_engine()
                self._created += 1
                return engine
        return self._pool.get()  # at capacity and all busy: wait for one to free

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        import numpy as np
        from PIL import Image

        rgb = np.array(Image.open(io.BytesIO(image_png)).convert("RGB"))
        bgr = rgb[:, :, ::-1]  # PaddleOCR expects OpenCV (BGR) order
        engine = self._acquire()
        try:
            results = engine.predict(bgr)
        finally:
            self._pool.put(engine)  # return the predictor for the next page

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
