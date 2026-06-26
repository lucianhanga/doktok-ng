"""Windowed transaction extraction over long documents (#314).

A financial document's transactions run to the last page, but one LLM call is capped at ``num_ctx``
(~16k chars). Slicing the head silently drops every transaction past the cap. Instead we split the
content into overlapping, line-aligned windows, extract each, and stitch the per-window results back
together - removing the duplicate rows that fall in each seam's overlap.

Both pieces are pure and deterministic, so they are unit-tested without a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from doktok_contracts.schemas import ExtractedRecord

# Keep windows as large as the model context allows (matches the providers' own _MAX_CHARS ceiling)
# so a typical statement needs the fewest calls. The LLM call dominates cost, not the window math.
WINDOW_CHARS = 16000
# Lines repeated across a seam so a transaction landing on a cut is still seen whole by one window.
# The duplicate appearance in the neighbouring window is removed by ``stitch_windows``.
OVERLAP_LINES = 12

# The normalized identity of a transaction: everything except the random id and the free-form
# raw_text. Two rows with the same key are the same transaction re-extracted across a seam.
_RecordKey = tuple[object, object, object, object, object, object]


def window_text(
    text: str, *, window_chars: int = WINDOW_CHARS, overlap_lines: int = OVERLAP_LINES
) -> list[str]:
    """Split ``text`` into line-aligned windows of <= ~``window_chars``, overlapping by
    ``overlap_lines`` lines.

    Splitting only on line boundaries keeps each transaction row intact. The overlap gives a row
    that lands on a cut a full appearance in the next window too. A single line longer than
    ``window_chars`` becomes its own (oversized) window rather than being dropped. Returns ``[]``
    for empty text and a single window when the whole document fits (so short docs are unchanged).
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    windows: list[str] = []
    start = 0
    total = len(lines)
    while start < total:
        size = 0
        end = start
        # Always take at least one line (handles an oversized single line), then fill to the cap.
        while end < total and (end == start or size + len(lines[end]) <= window_chars):
            size += len(lines[end])
            end += 1
        windows.append("".join(lines[start:end]))
        if end >= total:
            break
        start = max(end - overlap_lines, start + 1)  # step back for overlap, but always advance
    return windows


def _key(record: ExtractedRecord) -> _RecordKey:
    return (
        record.occurred_on,
        record.merchant_normalized,
        record.amount_minor,
        record.currency,
        record.direction,
        record.description,
    )


def _seam_overlap(prev: list[_RecordKey], cur: list[_RecordKey]) -> int:
    """Longest ``k`` where ``prev``'s last ``k`` keys equal ``cur``'s first ``k`` keys."""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if prev[-k:] == cur[:k]:
            return k
    return 0


def stitch_windows(per_window: Sequence[list[ExtractedRecord]]) -> list[ExtractedRecord]:
    """Concatenate per-window records, dropping each seam's duplicated overlap rows.

    Transactions are emitted in document order, so the rows a seam duplicates form a run that is
    both a suffix of one window and the prefix of the next. We drop the longest such matching prefix
    from each later window. Genuine repeats elsewhere in the document never line up across a seam,
    so they are kept (preserving exact SUM/COUNT).
    """
    merged: list[ExtractedRecord] = []
    prev_keys: list[_RecordKey] = []
    for window in per_window:
        cur_keys = [_key(r) for r in window]
        drop = _seam_overlap(prev_keys, cur_keys)
        merged.extend(window[drop:])
        prev_keys = cur_keys
    return merged
