"""Language detection and mapping to a PostgreSQL text-search configuration.

Used to extract lexical terms (significant lexemes, stopwords removed) in the document's language.
Detection is deterministic (fixed seed). Unknown/short text maps to the ``simple`` config, which
keeps all tokens without language-specific stemming or stopword removal.
"""

from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect

DetectorFactory.seed = 0  # deterministic detection

# ISO 639-1 -> a PostgreSQL text-search config that ships with standard installs.
_LANG_TO_PG_CONFIG: dict[str, str] = {
    "ar": "arabic",
    "ca": "catalan",
    "da": "danish",
    "nl": "dutch",
    "en": "english",
    "fi": "finnish",
    "fr": "french",
    "de": "german",
    "el": "greek",
    "hi": "hindi",
    "hu": "hungarian",
    "id": "indonesian",
    "ga": "irish",
    "it": "italian",
    "lt": "lithuanian",
    "ne": "nepali",
    "no": "norwegian",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sr": "serbian",
    "es": "spanish",
    "sv": "swedish",
    "ta": "tamil",
    "tr": "turkish",
}

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
    """Map an ISO language code to a PostgreSQL text-search config (``simple`` fallback)."""
    return _LANG_TO_PG_CONFIG.get(language, SIMPLE_CONFIG)
