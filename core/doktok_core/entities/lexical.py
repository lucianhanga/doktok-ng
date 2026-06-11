"""Plausibility filtering for lexical keyword terms (CUSTOM_TOKEN entities).

The lexical extractor produces frequency-ranked lexemes from a document. Many are noise: OCR garbage
character runs, abbreviations with no vowels, and - for documents OCR'd by a generative vision model
- hallucinated characters in a script the document is not written in (e.g. CJK in a German invoice).

These filters keep as many real words as possible (including proper nouns, which no dictionary would
contain) while dropping nonsense, so the token index and tag search stay meaningful.
"""

from __future__ import annotations

import unicodedata

from doktok_contracts.media import ExtractedTerm

# Latin vowels incl. the accented forms common to the supported European languages, plus 'y' (a
# vowel in English and several others). Used to require word-like structure in Latin tokens.
_VOWELS = set("aeiouyàáâãäåæèéêëìíîïòóôõöøùúûüýÿœ")

# Minimum share of vowels among a Latin token's letters. Real words - including consonant-heavy
# German compounds (e.g. "herbstmarkt", "schifffahrt") - sit above this; pure consonant noise does
# not. Kept low on purpose to favour recall.
_MIN_VOWEL_RATIO = 0.15

# A character repeated this many times in a row is an OCR-garbage signal. German tolerates triple
# letters after the 1996 spelling reform ("Schifffahrt"), so only 4+ in a row is rejected.
_MAX_CHAR_RUN = 4

# Language codes (langdetect ISO 639-1, incl. its zh variants) whose script is not Latin. A document
# in such a language yields tokens in that script, and a Latin document should not yield such tokens
# (this gate drops hallucinated CJK in Latin OCR). Everything not listed is treated as Latin.
_NON_LATIN_SCRIPTS: dict[str, str] = {
    "ru": "cyrillic",
    "uk": "cyrillic",
    "bg": "cyrillic",
    "zh": "cjk",
    "zh-cn": "cjk",
    "zh-tw": "cjk",
    "ja": "cjk",
    "ko": "cjk",
}


def _script_of(ch: str) -> str:
    """Coarse script bucket for a letter: 'latin', 'cyrillic', 'cjk', or 'other'."""
    code = ord(ch)
    if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF:
        return "cjk"
    name = unicodedata.name(ch, "")
    if name.startswith("LATIN"):
        return "latin"
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    return "other"


def is_meaningful_term(term: str, *, language: str) -> bool:
    """True if ``term`` looks like a real word in the document's language (not OCR/markup noise)."""
    letters = [c for c in term if c.isalpha()]
    if len(letters) < 3:
        return False
    # No character repeated 4+ times consecutively ("ssssos") - a reliable OCR-garbage signal.
    run = 1
    for prev, cur in zip(term, term[1:], strict=False):
        run = run + 1 if cur == prev else 1
        if run >= _MAX_CHAR_RUN:
            return False
    # Script must match the document: drop hallucinated CJK in a Latin doc (and vice versa for ru).
    expected = _NON_LATIN_SCRIPTS.get(language, "latin")
    if any(_script_of(c) not in (expected, "other") for c in letters):
        return False
    # Latin words need vowels; other scripts use different rules, so only gate Latin tokens.
    if expected == "latin":
        vowels = sum(1 for c in letters if c in _VOWELS)
        if vowels == 0 or vowels / len(letters) < _MIN_VOWEL_RATIO:
            return False
    return True


def meaningful_terms(
    terms: list[ExtractedTerm], *, language: str, limit: int
) -> list[ExtractedTerm]:
    """Keep the top ``limit`` plausible words, preserving the input (frequency) ranking."""
    kept: list[ExtractedTerm] = []
    for term in terms:
        if is_meaningful_term(term.term, language=language):
            kept.append(term)
            if len(kept) >= limit:
                break
    return kept
