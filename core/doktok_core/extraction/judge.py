"""Decide whether a page's embedded text or its OCR transcription is better.

Primary: ask the configured chat model (LLM-as-judge) which transcription is more accurate and
complete. Fallback (no model / error / unparseable): the deterministic ``text_quality`` heuristic.
"""

from __future__ import annotations

import logging

from doktok_contracts.ports import ChatModelProvider

from doktok_core.extraction.quality import text_quality

logger = logging.getLogger("doktok.extraction.judge")

_MAX_CHARS = 2000  # cap each candidate so the judge prompt stays small

_JUDGE_PROMPT = """You are comparing two transcriptions of the SAME scanned document page.
Choose the one that is more accurate and complete (more correct words, fewer garbled characters,
no missing text). Reply with EXACTLY one character: A or B. No explanation.

Transcription A:
\"\"\"
{a}
\"\"\"

Transcription B:
\"\"\"
{b}
\"\"\"

Which transcription is better, A or B?"""


def _llm_pick(embedded: str, ocr: str, chat_model: ChatModelProvider) -> bool:
    """Return True if the LLM prefers the OCR text (B), False for embedded (A)."""
    prompt = _JUDGE_PROMPT.format(a=embedded[:_MAX_CHARS], b=ocr[:_MAX_CHARS])
    response = chat_model.complete(prompt).strip().upper()
    for char in response:
        if char == "A":
            return False
        if char == "B":
            return True
    raise ValueError(f"unparseable judge response: {response!r}")


def choose_text(
    embedded: str,
    ocr: str,
    *,
    chat_model: ChatModelProvider | None = None,
) -> tuple[str, bool]:
    """Return ``(chosen_text, used_ocr)`` for an ambiguous page."""
    if chat_model is not None:
        try:
            used_ocr = _llm_pick(embedded, ocr, chat_model)
            return (ocr, True) if used_ocr else (embedded, False)
        except Exception:  # noqa: BLE001 - fall back to the heuristic on any judge failure
            logger.warning("LLM text judge failed; falling back to heuristic", exc_info=True)
    # Heuristic: prefer OCR only if it is strictly cleaner; otherwise keep the original.
    return (ocr, True) if text_quality(ocr) > text_quality(embedded) else (embedded, False)
