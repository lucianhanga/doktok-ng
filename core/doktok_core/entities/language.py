"""Language detection and mapping to a PostgreSQL text-search configuration.

Used to extract lexical terms (significant lexemes, stopwords removed) in the document's language.
Detection is deterministic (fixed seed). Unknown/short text maps to the ``simple`` config, which
keeps all tokens without language-specific stemming or stopword removal.
"""

from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0  # deterministic detection

# ISO 639-1 -> language name for which migration 0007 ships a non-stemming keyword text-search
# config (`doktok_kw_<name>` = simple template + that language's stopword list). These languages
# have stopword files in a standard PostgreSQL install. Others fall back to `simple` (no removal).
_KEYWORD_LANGS: dict[str, str] = {
    "da": "danish",
    "nl": "dutch",
    "en": "english",
    "fi": "finnish",
    "fr": "french",
    "de": "german",
    "hu": "hungarian",
    "it": "italian",
    "no": "norwegian",
    "pt": "portuguese",
    "ru": "russian",
    "es": "spanish",
    "sv": "swedish",
    "tr": "turkish",
}

KEYWORD_CONFIG_PREFIX = "doktok_kw_"
SIMPLE_CONFIG = "simple"


def detect_language(text: str) -> str:
    """Return an ISO 639-1 language code, or ``"unknown"`` when detection is not possible."""
    sample = text.strip()
    if len(sample) < 20:  # too little signal for reliable detection
        return "unknown"
    try:
        return str(detect(sample))
    except LangDetectException:
        return "unknown"


def pg_config_for(language: str) -> str:
    """Map an ISO code to a non-stemming keyword text-search config (``simple`` fallback)."""
    name = _KEYWORD_LANGS.get(language)
    return f"{KEYWORD_CONFIG_PREFIX}{name}" if name else SIMPLE_CONFIG
