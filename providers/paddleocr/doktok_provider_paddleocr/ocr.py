"""OCR via PaddleOCR (PP-OCRv5/v6), a drop-in ``OcrExtractor`` (ADR-0003).

PaddleOCR is a detection+recognition pipeline (DBNet + CTC), NOT a generative VLM, so it cannot
fall into the repeat-loops that produce garbage on sparse/stamp pages - it simply returns no text.
The output is kept shape-compatible with the previous OCR adapter: a single ``OcrPageResult`` with
the page text (lines joined in reading order) and a confidence (PaddleOCR's mean per-line score), so
nothing downstream in the pipeline changes.

PARALLELISM (M7.5): PaddleOCR CPU inference is single-threaded per ``predict`` AND holds the Python
GIL, so a thread pool of predictors collapses to ~1 core. To actually use the machine we run a pool
of ``pool_size`` worker PROCESSES (``ProcessPoolExecutor``), each owning one predictor built once in
an initializer; ``ocr_image`` dispatches a page to the pool and blocks for the result. Heavy work
runs in child interpreters (own GIL), so up to ``pool_size`` pages OCR truly in parallel. The
``paddleocr``/``paddlepaddle`` runtime is imported lazily, only inside the worker processes.
"""

from __future__ import annotations

import io
import logging
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from doktok_contracts.media import OcrPageResult

logger = logging.getLogger("doktok.ocr.paddle")

# Per-worker-process predictor, built once by the pool initializer (spawn -> fresh interpreter).
_WORKER_ENGINE: Any = None


def _cap_cpu_threads(n: int) -> None:
    """Pin this worker process to ``n`` math-library threads. Must run BEFORE paddle/numpy import,
    so the BLAS/OMP backends read it - this is the reliable, cross-version way to cap CPU on
    Apple Silicon (no MKL-DNN) where PaddleOCR exposes no effective cpu_threads kwarg. Keeping each
    pool worker at 1 thread means parallelism comes from the process pool, not oversubscription.
    """
    import os

    n = max(1, n)
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",  # Apple Accelerate
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[var] = str(n)


def _worker_init(lang: str, det_model: str, rec_model: str, cpu_threads: int) -> None:
    global _WORKER_ENGINE
    _cap_cpu_threads(cpu_threads)
    from paddleocr import PaddleOCR

    logger.info("PaddleOCR worker models (lang=%s, cpu_threads=%d)", lang, cpu_threads)
    # Skip the doc-orientation + unwarping models: rendered PDF pages are upright and flat, and
    # those models roughly triple per-page latency.
    _WORKER_ENGINE = PaddleOCR(
        lang=lang,
        text_detection_model_name=det_model,
        text_recognition_model_name=rec_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _worker_ocr(image_png: bytes) -> tuple[str, float | None]:
    return _run_ocr(_WORKER_ENGINE, image_png)


def _run_ocr(engine: Any, image_png: bytes) -> tuple[str, float | None]:
    """Decode the PNG, run the predictor, and assemble the page text + mean confidence.

    Pure given an ``engine`` (so it runs identically in-process for tests and in a worker process).
    """
    import numpy as np
    from PIL import Image

    rgb = np.array(Image.open(io.BytesIO(image_png)).convert("RGB"))
    bgr = rgb[:, :, ::-1]  # PaddleOCR expects OpenCV (BGR) order
    results = engine.predict(bgr)

    texts: list[str] = []
    scores: list[float] = []
    for item in results or []:
        page_text, page_scores = assemble_text(item)
        if page_text:
            texts.append(page_text)
        scores.extend(page_scores)
    confidence = sum(scores) / len(scores) if scores else None
    return "\n".join(texts), confidence


class PaddleOcr:
    """``OcrExtractor`` backed by PaddleOCR, parallelised across worker processes."""

    def __init__(
        self,
        *,
        lang: str = "german",
        det_model: str = "PP-OCRv5_mobile_det",
        rec_model: str = "latin_PP-OCRv5_mobile_rec",
        engine: Any = None,
        pool_size: int = 1,
        cpu_threads: int = 1,
    ) -> None:
        # ``lang='german'`` selects the Latin recognizer (German/English/most European scripts). The
        # mobile det+rec models are ~40% faster than the medium defaults at ~0.98 confidence.
        self._lang = lang
        self._det_model = det_model
        self._rec_model = rec_model
        # An injected predictor (tests) runs IN-PROCESS - no subprocess pool is started.
        self._engine = engine
        self._pool_size = max(1, pool_size)
        # Threads per worker process; with one core per worker, real parallelism is the pool size.
        self._cpu_threads = max(1, cpu_threads)
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()  # guards lazy pool creation

    def reconfigure(self, pool_size: int) -> None:
        """Resize the worker pool live (M7.6 settings). Shuts the current pool down so the next
        ``ocr_image`` rebuilds it at the new size. Call only when no OCR is in flight (the worker
        does this between ingest scans). No-op if the size is unchanged or an engine is injected."""
        target = max(1, pool_size)
        with self._lock:
            if self._engine is not None or target == self._pool_size:
                self._pool_size = target
                return
            old = self._pool
            self._pool = None
            self._pool_size = target
        if old is not None:
            old.shutdown(wait=True)  # safe between scans: no page is being OCR'd
            logger.info("PaddleOCR pool resized to %d workers", target)

    def shutdown(self) -> None:
        """Tear down the worker pool so its model-laden spawn processes do not leak (become
        launchd-owned orphans, ~1 GB each) when the worker stops. Call once on worker exit; safe
        when no pool was ever started or an in-process engine is used."""
        with self._lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=True)
            logger.info("PaddleOCR process pool shut down")

    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    logger.info("starting PaddleOCR process pool (workers=%d)", self._pool_size)
                    self._pool = ProcessPoolExecutor(
                        max_workers=self._pool_size,
                        initializer=_worker_init,
                        initargs=(
                            self._lang,
                            self._det_model,
                            self._rec_model,
                            self._cpu_threads,
                        ),
                    )
        return self._pool

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        if self._engine is not None:
            text, confidence = _run_ocr(self._engine, image_png)  # in-process (tests)
        else:
            # Dispatch to a worker process; many caller threads submitting here drive real
            # cross-page parallelism across ``pool_size`` cores.
            text, confidence = self._executor().submit(_worker_ocr, image_png).result()
        return OcrPageResult(text=text, confidence=confidence)


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
