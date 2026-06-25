"""RapidOCR adapter for the OcrExtractor port (M17 #375).

RapidOCR runs the SAME PP-OCR detection+recognition models as the PaddleOCR adapter, but exported to
ONNX and executed via ONNXRuntime (optionally the OpenVINO provider on Intel). On weak CPUs
this is markedly faster and lighter than PaddlePaddle's native CPU path - and it sidesteps the
PaddlePaddle oneDNN/PIR crash that forces mkldnn off on N95/Alder-Lake-N (where Paddle then crawls).

Output is shape-compatible with the PaddleOCR adapter: a single ``OcrPageResult`` per page with the
joined text, mean confidence, and per-line boxes. The engine is parallelised across worker processes
(one model-laden process per pool slot), mirroring the PaddleOCR adapter so the worker can resize
pool live and tear it down cleanly. The ``rapidocr`` runtime is imported lazily, inside the workers.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from doktok_contracts.media import OcrPageResult, OcrTextLine

logger = logging.getLogger("doktok.ocr.rapid")

_WORKER_ENGINE: Any = None


def _cap_cpu_threads(n: int) -> None:
    """Pin this process to ``n`` math-library threads (must run BEFORE numpy/onnxruntime import) so
    ``pool_size`` processes do not oversubscribe the cores. Mirrors the PaddleOCR adapter."""
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "ORT_NUM_THREADS",
    ):
        os.environ[var] = str(n)


def _make_engine(backend: str) -> Any:
    """Construct a RapidOCR engine for the chosen backend ('onnxruntime' or 'openvino')."""
    if backend == "openvino":
        from rapidocr_openvino import RapidOCR
    else:
        from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _worker_init(lang: str, cpu_threads: int, backend: str) -> None:
    global _WORKER_ENGINE
    _cap_cpu_threads(cpu_threads)
    logger.info(
        "RapidOCR worker engine (backend=%s, lang=%s, cpu_threads=%d)", backend, lang, cpu_threads
    )
    _WORKER_ENGINE = _make_engine(backend)


def _worker_ocr(image_png: bytes) -> OcrPageResult:
    return _run_ocr(_WORKER_ENGINE, image_png)


_BBox = tuple[float, float, float, float]


def _ordered(result: Any) -> list[tuple[str, float | None, _BBox]]:
    """RapidOCR returns a list of ``[box, text, score]`` (box = 4 [x,y] points), or None. Return the
    lines as (text, score, axis-aligned bbox) in reading order (top-to-bottom, left-to-right)."""
    items: list[tuple[str, float | None, _BBox]] = []
    for entry in result or []:
        box, text, score = entry[0], str(entry[1]), entry[2]
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        try:
            conf: float | None = float(score)
        except (TypeError, ValueError):
            conf = None
        items.append((text, conf, bbox))
    # Reading order: row band (top), then left-to-right.
    items.sort(key=lambda it: (round(it[2][1] / 10.0), it[2][0]))
    return items


def _run_ocr(engine: Any, image_png: bytes) -> OcrPageResult:
    """Decode the PNG, run RapidOCR, and assemble page text + mean confidence + per-line boxes."""
    import numpy as np
    from PIL import Image

    rgb = np.array(Image.open(io.BytesIO(image_png)).convert("RGB"))
    height, width = rgb.shape[:2]
    bgr = rgb[:, :, ::-1]  # RapidOCR/OpenCV convention
    result, _elapse = engine(bgr)

    ordered = _ordered(result)
    texts = [t for t, _, _ in ordered if t.strip()]
    scores = [s for _, s, _ in ordered if s is not None]
    lines = [
        OcrTextLine(text=t, x0=b[0], y0=b[1], x1=b[2], y1=b[3]) for t, _, b in ordered if t.strip()
    ]
    return OcrPageResult(
        text="\n".join(texts),
        confidence=(sum(scores) / len(scores)) if scores else None,
        lines=lines,
        width=int(width),
        height=int(height),
    )


class RapidOcr:
    """``OcrExtractor`` backed by RapidOCR (ONNX/OpenVINO), parallelised across worker processes."""

    def __init__(
        self,
        *,
        lang: str = "german",
        engine: Any = None,
        pool_size: int = 1,
        cpu_threads: int = 1,
        backend: str = "onnxruntime",
    ) -> None:
        self._lang = lang
        self._engine = engine  # injected predictor (tests) -> runs in-process, no pool
        self._pool_size = max(1, pool_size)
        self._cpu_threads = max(1, cpu_threads)
        self._backend = backend
        self._closed = False
        self._pool: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()

    def reconfigure(self, pool_size: int) -> None:
        """Resize the worker pool live (between ingest scans). No-op if unchanged or injected."""
        target = max(1, pool_size)
        with self._lock:
            if self._engine is not None or target == self._pool_size:
                self._pool_size = target
                return
            old = self._pool
            self._pool = None
            self._pool_size = target
        if old is not None:
            old.shutdown(wait=True)
            logger.info("RapidOCR pool resized to %d workers", target)

    def shutdown(self) -> None:
        """Tear down the worker pool so its model-laden processes do not leak on worker stop."""
        with self._lock:
            self._closed = True
            pool = self._pool
            self._pool = None
        if pool is not None:
            pool.shutdown(wait=True)
            logger.info("RapidOCR process pool shut down")

    def _executor(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._closed:
                raise RuntimeError("RapidOCR pool is shut down")
            if self._pool is None:
                logger.info("starting RapidOCR process pool (workers=%d)", self._pool_size)
                self._pool = ProcessPoolExecutor(
                    max_workers=self._pool_size,
                    initializer=_worker_init,
                    initargs=(self._lang, self._cpu_threads, self._backend),
                )
            return self._pool

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        if self._engine is not None:  # in-process (tests)
            return _run_ocr(self._engine, image_png)
        return self._executor().submit(_worker_ocr, image_png).result()
