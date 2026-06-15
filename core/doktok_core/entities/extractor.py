"""Rule-based entity extraction (M5, brief section 19).

Deterministic regex extraction for the types that patterns capture reliably and usefully: EMAIL and
URL. MONEY / DATE / INVOICE_ID / CONTRACT_ID were dropped (M8.x, #312) - their regex matches were
~90% noise on real documents (monetary data lives in extracted records, dates in metadata). PERSON/
ORG/GPE come from NER (spaCy or LLM-assisted); the ``EntityExtractor`` port lets that adapter be
swapped in without touching core.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType


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
