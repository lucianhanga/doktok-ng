"""LLM-assisted named-entity recognition support (M7.4).

PERSON / ORG / GPE can't be found with regex (they need to recognise *names*), so an LLM-assisted
``EntityNerExtractor`` fills them. NER and the rule-based ``EntitiesFeature`` write to the same
``document_entities`` table but own DISJOINT entity-type sets, so each can re-run (backfill, retry,
version bump) without clobbering the other's rows.
"""

from __future__ import annotations

import re

from doktok_contracts.schemas import EntityType

# The entity types owned by the NER feature. Everything else belongs to the rule-based extractor.
NER_ENTITY_TYPES: tuple[EntityType, ...] = (
    EntityType.PERSON,
    EntityType.ORG,
    EntityType.GPE,
)

_WHITESPACE = re.compile(r"\s+")


def normalize_ner_name(name: str) -> str:
    """A whitespace-collapsed, casefolded key for de-duplicating a name across its mentions."""
    return _WHITESPACE.sub(" ", name.strip()).casefold()
