"""A cheap, deterministic text-quality heuristic (0..1).

Used to decide whether a PDF page's embedded text layer is good enough to keep, versus running OCR.
Clean prose scores high; OCR garbage (isolated symbols, broken tokens) scores low. No dependencies.
"""

from __future__ import annotations

_STRIP = ".,;:!?()[]{}\"'`-—–…/\\|"


def _is_wordlike(token: str) -> bool:
    stripped = token.strip(_STRIP)
    if len(stripped) < 2:
        return False
    letters = sum(1 for c in stripped if c.isalpha())
    return letters / len(stripped) >= 0.6


def text_quality(text: str) -> float:
    """Return a 0..1 quality score. Higher means cleaner, more word-like text."""
    text = text.strip()
    if not text:
        return 0.0
    tokens = text.split()
    if not tokens:
        return 0.0
    wordlike_ratio = sum(1 for t in tokens if _is_wordlike(t)) / len(tokens)
    nonspace = [c for c in text if not c.isspace()]
    alpha_density = sum(1 for c in nonspace if c.isalpha()) / len(nonspace) if nonspace else 0.0
    return round(0.6 * wordlike_ratio + 0.4 * alpha_density, 4)
