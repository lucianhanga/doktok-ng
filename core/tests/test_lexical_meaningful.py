"""Unit tests for lexical keyword plausibility filtering (no DB needed)."""

from __future__ import annotations

import pytest
from doktok_contracts.media import ExtractedTerm
from doktok_core.entities.lexical import is_meaningful_term, meaningful_terms


@pytest.mark.parametrize(
    "word",
    [
        "münchen",  # proper noun with umlaut
        "investment",
        "köglsperger",  # surname
        "apotheke",
        "versicherung",
        "herbstmarkt",  # consonant-heavy German compound
        "schifffahrt",  # legal triple-f
        "portfolio",
        "rechnung",
    ],
)
def test_real_words_are_kept(word: str) -> None:
    assert is_meaningful_term(word, language="de")


@pytest.mark.parametrize(
    ("word", "language"),
    [
        ("td", "de"),  # HTML tag / too short
        ("tr", "de"),
        ("hr", "de"),
        ("bzw", "de"),  # no vowel
        ("str", "de"),  # no vowel
        ("gmbh", "de"),  # no vowel
        ("ssssos", "de"),  # 4+ char run (OCR garbage)
        ("硬化", "de"),  # CJK hallucination in a German doc
        ("黑龙江", "de"),
        ("荣耀", "en"),
    ],
)
def test_noise_is_dropped(word: str, language: str) -> None:
    assert not is_meaningful_term(word, language=language)


def test_meaningful_terms_filters_and_caps_preserving_order() -> None:
    terms = [
        ExtractedTerm(term="td", frequency=99),
        ExtractedTerm(term="investment", frequency=50),
        ExtractedTerm(term="硬化", frequency=40),
        ExtractedTerm(term="portfolio", frequency=30),
        ExtractedTerm(term="rechnung", frequency=20),
    ]
    kept = meaningful_terms(terms, language="de", limit=2)
    assert [t.term for t in kept] == ["investment", "portfolio"]  # noise skipped, order + cap kept
