"""Rule-based entity extraction (M5, brief section 19; expanded #518 Phase 1).

Deterministic extraction for the types that patterns capture reliably and usefully: EMAIL and URL
by regex, plus the VALIDATED structured identifiers (PHONE / IBAN / VAT_ID / TAX_NUMBER /
REGISTRATION_NUMBER via checksum/libphonenumber validators in ``entities.validated``, and
ADDRESS / POSTAL_CODE via the libpostal engine extra in ``entities.address`` - skipped gracefully
when libpostal is not installed). MONEY / DATE / INVOICE_ID / CONTRACT_ID were dropped (M8.x,
#312) - their regex matches were ~90% noise on real documents (monetary data lives in extracted
records, dates in metadata). PERSON/ORG/GPE come from NER (spaCy or LLM-assisted); the
``EntityExtractor`` port lets that adapter be swapped in without touching core.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType

from doktok_core.entities.address import extract_addresses
from doktok_core.entities.validated import extract_validated_entities


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
    """``EntityExtractor`` combining deterministic regex rules (EMAIL/URL) with the validated
    structured-identifier detectors (#518 Phase 1). Every non-regex type is gated by a real
    validator (checksum / libphonenumber / libpostal / required context cue), never a bare
    pattern hit, so precision stays near 100%."""

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
        found.extend(extract_validated_entities(text))
        found.extend(extract_addresses(text))  # no-op (logged once) without libpostal
        return found
