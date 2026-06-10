"""Deterministic fixed-window chunker (M4).

Splits text into overlapping character windows. Deterministic and reproducible (brief section 15):
the same input always yields the same chunks. Token counts are estimated (no tokenizer dependency).
"""

from __future__ import annotations

from doktok_contracts.media import TextChunk


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FixedWindowChunker:
    def __init__(self, max_chars: int = 1200, overlap: int = 200) -> None:
        if overlap >= max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        self._max_chars = max_chars
        self._overlap = overlap

    def chunk(self, text: str) -> list[TextChunk]:
        if not text.strip():
            return []
        step = self._max_chars - self._overlap
        chunks: list[TextChunk] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + self._max_chars, n)
            piece = text[start:end]
            if piece.strip():
                chunks.append(
                    TextChunk(
                        text=piece,
                        token_count=_estimate_tokens(piece),
                        start_offset=start,
                        end_offset=end,
                    )
                )
            if end == n:
                break
            start += step
        return chunks
