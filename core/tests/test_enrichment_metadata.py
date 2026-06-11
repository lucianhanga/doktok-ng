"""Tests for hard validation/normalization of extracted metadata (M6.2)."""

from __future__ import annotations

from datetime import date

from doktok_contracts.media import ExtractedMetadata
from doktok_core.enrichment import normalize_metadata


def _raw(**kw: str | None) -> ExtractedMetadata:
    base: dict[str, str | None] = {
        "title": "A title",
        "document_date": "2026-01-15",
        "location": "Hamburg",
        "summary": "A summary.",
    }
    base.update(kw)
    return ExtractedMetadata(**base)  # type: ignore[arg-type]


def test_parses_iso_date() -> None:
    assert normalize_metadata(_raw()).document_date == date(2026, 1, 15)


def test_na_and_malformed_dates_become_none() -> None:
    assert normalize_metadata(_raw(document_date="n/a")).document_date is None
    assert normalize_metadata(_raw(document_date="March 2026")).document_date is None
    assert normalize_metadata(_raw(document_date="2026-02-30")).document_date is None  # invalid day
    assert normalize_metadata(_raw(document_date=None)).document_date is None


def test_na_location_becomes_none() -> None:
    assert normalize_metadata(_raw(location="n/a")).location is None
    assert normalize_metadata(_raw(location="  ")).location is None
    assert normalize_metadata(_raw(location="Berlin")).location == "Berlin"


def test_title_is_word_capped() -> None:
    long_title = " ".join(f"w{i}" for i in range(30))
    out = normalize_metadata(_raw(title=long_title))
    assert out.title is not None and len(out.title.split()) == 15


def test_empty_summary_becomes_none() -> None:
    assert normalize_metadata(_raw(summary="")).summary is None
