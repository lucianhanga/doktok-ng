"""Tag-name normalization + the color palette (epic #543).

The normalized key dedups case/format variants of the same tag: NFKC, casefold, emoji stripped,
trimmed, whitespace collapsed. Display names keep the user's original formatting (emoji allowed).
"""

from __future__ import annotations

import re
import unicodedata

# The starter palette: TOKENS only (never freeform hex), mapped per theme in the UI so contrast
# stays WCAG-correct in light and dark (#543 decision; the UI mirrors this list).
TAG_PALETTE: tuple[str, ...] = (
    "slate",
    "gray",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "violet",
    "pink",
)

_WS = re.compile(r"\s+")
# Broad emoji/pictograph ranges (kept ASCII-only in the dedup key).
_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]+")


def normalize_tag_name(name: str) -> str:
    """The dedup key for a display name: NFKC + casefold + emoji stripped + collapsed ws."""
    normalized = unicodedata.normalize("NFKC", name).casefold()
    normalized = _EMOJI.sub("", normalized)
    return _WS.sub(" ", normalized).strip()
