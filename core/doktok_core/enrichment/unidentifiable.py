"""Deterministic detector for "unidentifiable" documents (ADR-0017, M7.3 Phase 2).

A document is unidentifiable when extraction succeeded but the content is not meaningful (codes,
numbers, OCR noise - no coherent text), e.g. a scanned card the model could only title
"Unidentifiable Document". We detect this from the extracted text with the same cheap, local,
deterministic ``text_quality`` heuristic the OCR gate uses (clean prose scores high; symbol/number
soup scores low) - NOT by trusting the model to emit the literal word "unidentifiable".
"""

from __future__ import annotations

from doktok_core.extraction.quality import text_quality

# Below this text-quality score the content is treated as not meaningful. Clean prose scores ~0.7+;
# tune via the OCR-quality intuition. Conservative so real (if sparse) documents are not flagged.
UNIDENTIFIABLE_MAX_QUALITY = 0.35
# Don't judge near-empty content - too little signal to call it either way; leave it unassessed.
_MIN_CHARS = 40


def detect_unidentifiable(content: str) -> bool | None:
    """Return True if the content is unidentifiable, False if it looks meaningful, None if there is
    too little text to decide (the caller leaves the marker unassessed)."""
    stripped = content.strip()
    if len(stripped) < _MIN_CHARS:
        return None
    return text_quality(stripped) < UNIDENTIFIABLE_MAX_QUALITY
