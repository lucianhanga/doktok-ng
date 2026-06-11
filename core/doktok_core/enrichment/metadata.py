"""Hard validation/normalization of LLM-extracted metadata (M6.2).

The model is asked nicely for an ISO date, a short title, and ``n/a`` when undeterminable - but the
code, not the prompt, is the guarantee. Malformed dates become NULL, ``n/a``/empty become NULL, and
the title is capped. Pure and deterministic, so it is unit-tested without a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from doktok_contracts.media import ExtractedMetadata

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NA = {"n/a", "na", "none", "null", "unknown", "undetermined", ""}
_TITLE_MAX_WORDS = 15


@dataclass
class NormalizedMetadata:
    title: str | None
    document_date: date | None
    location: str | None
    summary: str | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return None if text.lower() in _NA else text


def _parse_date(value: str | None) -> date | None:
    text = _clean(value)
    if text is None or not _ISO_DATE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:  # e.g. 2026-02-30
        return None


def _title(value: str | None) -> str | None:
    text = _clean(value)
    if text is None:
        return None
    words = text.split()
    return " ".join(words[:_TITLE_MAX_WORDS]) if len(words) > _TITLE_MAX_WORDS else text


def normalize_metadata(raw: ExtractedMetadata) -> NormalizedMetadata:
    return NormalizedMetadata(
        title=_title(raw.title),
        document_date=_parse_date(raw.document_date),
        location=_clean(raw.location),
        summary=_clean(raw.summary),
    )
