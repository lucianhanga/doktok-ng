"""Category-name normalization for the controlled vocabulary (M6.2).

Normalizing before dedup is what stops near-duplicates like "Invoices"/"Invoice" from both entering
the bounded vocabulary. Pure and deterministic, so it is unit-tested without a model.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")

MAX_CATEGORIES_PER_TENANT = 20
MAX_CATEGORIES_PER_DOCUMENT = 5


def normalize_category(name: str) -> str:
    """Casefold, strip punctuation, collapse whitespace, and de-pluralize a trailing 's'."""
    text = _PUNCT.sub(" ", name).casefold().strip()
    text = _WS.sub(" ", text)
    if len(text) > 3 and text.endswith("s") and not text.endswith("ss"):
        text = text[:-1]
    return text
