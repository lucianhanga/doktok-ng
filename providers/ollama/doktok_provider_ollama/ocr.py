"""OCR via a local Ollama vision model (ADR-0003, configurable DOKTOK_OCR_MODEL).

Sends a page image to Ollama's ``/api/generate`` with a faithful-transcription prompt. Talks only to
the local Ollama endpoint (no external egress). The model does not report a confidence, so
``confidence`` is left ``None``.
"""

from __future__ import annotations

import base64
import logging

import httpx
from doktok_contracts.media import OcrPageResult

logger = logging.getLogger("doktok.ocr")

OCR_PROMPT = (
    "Extract all text from this document image exactly as it appears. "
    "Preserve reading order (top to bottom, left to right; columns in column order). "
    "Format headings as Markdown headings and tables as Markdown tables. "
    "Do not summarize, interpret, or add commentary. Output only the extracted content."
)


class OllamaVisionOcr:
    """``OcrExtractor`` backed by an Ollama vision model.

    A single page needs only a modest context (image tiles + prompt + output ~= 4.4k tokens); the
    default ``num_ctx`` of 8192 avoids the ~1 GB KV cache a 32k context reserves. ``num_predict``
    caps per-page output so a garbled/dense page can't loop unbounded. If a page hits that cap
    (``done: false``), we keep the partial transcription and warn - a truncated page is far better
    than failing the whole document's ingestion.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        num_ctx: int = 8192,
        num_predict: int = 8192,
        keep_alive: str = "5m",
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._num_ctx = num_ctx
        self._num_predict = num_predict
        self._keep_alive = keep_alive

    def ocr_image(self, image_png: bytes) -> OcrPageResult:
        payload = {
            "model": self._model,
            "prompt": OCR_PROMPT,
            "images": [base64.b64encode(image_png).decode("ascii")],
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
                # Penalize repetition so the model can't loop a line forever on a sparse/stamp page
                # (a known glm-ocr failure that otherwise fills the page with garbage).
                "repeat_penalty": 1.3,
                "repeat_last_n": 64,
            },
        }
        response = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=self._timeout)
        response.raise_for_status()
        body = response.json()
        text = str(body.get("response", "")).strip()
        if body.get("done") is False:
            logger.warning(
                "OCR truncated a page at the %d-token output cap (%d chars kept)",
                self._num_predict,
                len(text),
            )
        # Safety net: collapse any runaway repetition the penalty didn't prevent, so garbage never
        # reaches content.md (and the enrichment title/summary).
        return OcrPageResult(text=trim_runaway_repetition(text), confidence=None)


def trim_runaway_repetition(text: str, *, max_repeats: int = 3) -> str:
    """Collapse a short cycle of identical lines that repeats more than ``max_repeats`` times.

    OCR repeat-loops emit the same 1-4 lines hundreds of times; keep a few and drop the rest. Real
    tables have distinct rows, so they are never collapsed.
    """
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        collapsed = False
        for period in range(1, 5):
            cycle = lines[i : i + period]
            if not any(line.strip() for line in cycle):
                continue
            reps, j = 1, i + period
            while lines[j : j + period] == cycle and j + period <= n:
                reps += 1
                j += period
            if reps > max_repeats:
                out.extend(cycle * max_repeats)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(lines[i])
            i += 1
    return "\n".join(out)
