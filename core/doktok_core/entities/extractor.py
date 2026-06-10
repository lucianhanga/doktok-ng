"""Rule-based entity extraction (M5, brief section 19).

Deterministic regex extraction for the types that patterns capture reliably: EMAIL, URL, MONEY,
DATE, INVOICE_ID, CONTRACT_ID. PERSON/ORG/GPE need NER (spaCy or LLM-assisted) and are a documented
follow-up; the ``EntityExtractor`` port lets that adapter be swapped in without touching core.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType


def _identity(value: str) -> str:
    return value.strip()


def _lower(value: str) -> str:
    return value.strip().lower()


def _url(value: str) -> str:
    # URLs often abut sentence punctuation; trim trailing punctuation before normalizing.
    return value.strip().rstrip(".,;:!?)").lower()


@dataclass
class _Rule:
    entity_type: EntityType
    pattern: re.Pattern[str]
    value_group: int  # which capture group holds the normalized value (0 = whole match)
    normalize: Callable[[str], str]


_RULES: list[_Rule] = [
    _Rule(
        EntityType.EMAIL, re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), 0, _lower
    ),
    _Rule(EntityType.URL, re.compile(r"https?://[^\s<>()\[\]]+"), 0, _url),
    _Rule(
        EntityType.MONEY,
        re.compile(
            r"[$€£]\s?\d[\d,]*(?:\.\d+)?"
            r"|\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|dollars|euros|pounds)\b",
            re.IGNORECASE,
        ),
        0,
        _identity,
    ),
    _Rule(EntityType.DATE, re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 0, _identity),
    _Rule(EntityType.DATE, re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"), 0, _identity),
    _Rule(
        EntityType.INVOICE_ID,
        re.compile(
            r"invoice\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,})", re.IGNORECASE
        ),
        1,
        lambda v: v.strip().upper(),
    ),
    _Rule(
        EntityType.CONTRACT_ID,
        re.compile(
            r"contract\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-]{2,})",
            re.IGNORECASE,
        ),
        1,
        lambda v: v.strip().upper(),
    ),
]


class RegexEntityExtractor:
    """``EntityExtractor`` using deterministic regex patterns."""

    def extract(self, text: str) -> list[ExtractedEntity]:
        found: list[ExtractedEntity] = []
        for rule in _RULES:
            for match in rule.pattern.finditer(text):
                raw = match.group(rule.value_group)
                if not raw:
                    continue
                found.append(
                    ExtractedEntity(
                        entity_text=match.group(0),
                        entity_type=rule.entity_type,
                        normalized_value=rule.normalize(raw),
                        start_offset=match.start(),
                        end_offset=match.end(),
                    )
                )
        return found
