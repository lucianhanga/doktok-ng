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
# Same approach as enrichment.categories.normalize_category: everything that is not a word
# character or whitespace becomes a separator, so 'hanga,lucian' tokenizes like 'hanga lucian'.
_PUNCTUATION = re.compile(r"[^\w\s]")


def normalize_ner_name(name: str) -> str:
    """A whitespace-collapsed, casefolded key for de-duplicating a name across its mentions."""
    return _WHITESPACE.sub(" ", name.strip()).casefold()


def normalize_entity_name(name: str) -> str:
    """The token-set sort key for entity resolution (#508): casefold, strip punctuation,
    collapse whitespace, then SORT and DEDUPE the tokens.

    This is deliberately stronger than ``normalize_ner_name`` (which stays the per-document
    display normalization): deriving the KG node key from this value collapses word-order and
    punctuation variants at write time - 'lucian hanga', 'hanga,lucian' and 'hanga lucian' all
    key to 'hanga lucian'. Single-token concatenations ('lucianhanga') and typos ('hanja lucian')
    stay distinct keys; those are the fuzzy tier's job (``knowledge_graph.entity_resolution``).

    Falls back to the casefolded, whitespace-collapsed input when punctuation-stripping leaves
    nothing (an all-punctuation value must not collapse into the empty key).
    """
    text = _PUNCTUATION.sub(" ", name).casefold().strip()
    tokens = sorted(set(text.split()))
    if not tokens:
        return normalize_ner_name(name)
    return " ".join(tokens)
