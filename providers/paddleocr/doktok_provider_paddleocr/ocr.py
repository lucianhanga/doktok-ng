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

from doktok_contracts.media import OcrPageResult, OcrTextLine

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


def _worker_init(
    lang: str, det_model: str, rec_model: str, cpu_threads: int, preprocess: bool
) -> None:
    global _WORKER_ENGINE
    _cap_cpu_threads(cpu_threads)
    from paddleocr import PaddleOCR

    logger.info(
        "PaddleOCR worker models (lang=%s, cpu_threads=%d, preprocess=%s)",
        lang,
        cpu_threads,
        preprocess,
    )
    # Standard path skips the unwarping + textline models (rendered pages are upright/flat and they
    # ~triple per-page latency). The Enhanced path enables them to fix curved/upside-down scans.
    # Page orientation (90/180/270) is handled by the explicit 4-way vote, not PaddleOCR's own
    # doc-orientation classifier (which proved unreliable), so that model stays off.
    _WORKER_ENGINE = PaddleOCR(
        lang=lang,
        text_detection_model_name=det_model,
        text_recognition_model_name=rec_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=preprocess,
        use_textline_orientation=preprocess,
    )


def _worker_ocr(image_png: bytes) -> OcrPageResult:
    return _run_ocr(_WORKER_ENGINE, image_png)


def _rotate_png(image_png: bytes, degrees: int) -> bytes:
    """Rotate a PNG clockwise by 90/180/270 (same convention as the searchable-PDF builder, so the
    chosen orientation's boxes line up with the rotated image)."""
    import io as _io

    from PIL import Image

    with Image.open(_io.BytesIO(image_png)) as image:
        rotated = image.rotate(-degrees, expand=True)  # PIL is counter-clockwise; negate for cw
        buffer = _io.BytesIO()
        rotated.save(buffer, format="PNG")
        return buffer.getvalue()


def _score(page: OcrPageResult) -> float:
    """Rank an orientation: a correctly-upright page reads more confident text. Combine mean
    confidence with line count so a sideways page (few/low-confidence lines) loses."""
    if not page.lines or page.confidence is None:
        return 0.0
    return page.confidence * len(page.lines)


def _worker_ocr_vote(image_png: bytes) -> OcrPageResult:
    return _run_ocr_vote(_WORKER_ENGINE, image_png)


def _run_ocr_vote(engine: Any, image_png: bytes) -> OcrPageResult:
    """4-way orientation vote (Enhanced): OCR the page at 0/90/180/270 and keep the orientation that
    reads the most confident text. The winner's text/boxes are in that rotated frame; ``rotation``
    records the angle so the searchable PDF + overlay can present it upright."""
    best: OcrPageResult | None = None
    best_angle = 0
    best_score = -1.0
    for angle in (0, 90, 180, 270):
        png = image_png if angle == 0 else _rotate_png(image_png, angle)
        page = _run_ocr(engine, png)
        score = _score(page)
        if score > best_score:
            best, best_angle, best_score = page, angle, score
    assert best is not None
    best.rotation = best_angle
    return best


def _run_ocr(engine: Any, image_png: bytes) -> OcrPageResult:
    """Decode the PNG, run the predictor, and assemble the page text + mean confidence + per-line
    boxes (in image pixels, for the positioned searchable-PDF text layer) + the image pixel size.

    Pure given an ``engine`` (so it runs identically in-process for tests and in a worker process).
    """
    import numpy as np
    from PIL import Image

    rgb = np.array(Image.open(io.BytesIO(image_png)).convert("RGB"))
    height, width = rgb.shape[:2]
    bgr = rgb[:, :, ::-1]  # PaddleOCR expects OpenCV (BGR) order
    results = engine.predict(bgr)

    texts: list[str] = []
    scores: list[float] = []
    lines: list[OcrTextLine] = []
    for item in results or []:
        page_text, page_scores = assemble_text(item)
        if page_text:
            texts.append(page_text)
        scores.extend(page_scores)
        for text, (x0, y0, x1, y1) in assemble_lines(item):
            lines.append(OcrTextLine(text=text, x0=x0, y0=y0, x1=x1, y1=y1))
    confidence = sum(scores) / len(scores) if scores else None
    return OcrPageResult(
        text="\n".join(texts),
        confidence=confidence,
        lines=lines,
        width=int(width),
        height=int(height),
    )


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
        preprocess: bool = False,
        orient_vote: bool = False,
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
        # Enhanced path: unwarp + textline-orientation models, and the 4-way orientation vote.
        self._preprocess = preprocess
        self._orient_vote = orient_vote
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
                            self._preprocess,
                        ),
                    )
        return self._pool

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        if self._engine is not None:  # in-process (tests)
            return (
                _run_ocr_vote(self._engine, image_png)
                if self._orient_vote
                else _run_ocr(self._engine, image_png)
            )
        # Dispatch to a worker process; many caller threads submitting here drive real
        # cross-page parallelism across ``pool_size`` cores. The 4-way vote (Enhanced) OCRs each
        # page at all four orientations, so it is ~4x slower - opt-in only.
        worker = _worker_ocr_vote if self._orient_vote else _worker_ocr
        return self._executor().submit(worker, image_png).result()


def _as_list(value: Any) -> list[Any]:
    return [] if value is None else list(value)


_BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1) in image pixels


def _ordered(result: Any) -> list[tuple[str, float | None, _BBox | None]]:
    """Recognized lines in reading order (top-to-bottom, left-to-right) with score + bbox.

    ``result`` is PaddleOCR's per-image result (a dict with ``rec_texts``/``rec_scores`` and either
    ``rec_boxes`` [x1,y1,x2,y2] or ``rec_polys``/``dt_polys`` [[x,y]x4]).
    """
    texts = _as_list(result.get("rec_texts"))
    scores = [float(s) for s in _as_list(result.get("rec_scores"))]
    boxes = result.get("rec_boxes")
    polys = result.get("rec_polys")
    if polys is None:
        polys = result.get("dt_polys")

    def bbox(i: int) -> _BBox | None:
        if boxes is not None and i < len(boxes):
            b = boxes[i]
            return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        if polys is not None and i < len(polys):
            pts = polys[i]
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))
        return None

    def top_left(i: int) -> tuple[float, float]:
        bb = bbox(i)
        return (bb[1], bb[0]) if bb else (float(i), 0.0)

    # Bucket the y-coordinate so lines on the same row sort left-to-right.
    order = sorted(range(len(texts)), key=lambda i: (round(top_left(i)[0] / 10.0), top_left(i)[1]))
    return [(str(texts[i]), (scores[i] if i < len(scores) else None), bbox(i)) for i in order]


def assemble_text(result: Any) -> tuple[str, list[float]]:
    """Join a result's recognized lines in reading order; returns the text + per-line scores."""
    items = _ordered(result)
    text = "\n".join(t for t, _, _ in items)
    scores = [s for _, s, _ in items if s is not None]
    return text, scores


def assemble_lines(result: Any) -> list[tuple[str, _BBox]]:
    """Recognized lines (non-empty, with a box) in reading order, for the positioned text layer."""
    return [(t, bb) for t, _, bb in _ordered(result) if bb is not None and t.strip()]
