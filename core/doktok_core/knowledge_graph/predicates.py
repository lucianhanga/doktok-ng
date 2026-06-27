"""Closed predicate vocabulary for KAG Phase 2 relation extraction (household corpus).

This is the single source of truth for the allowed relation predicates and their
subject/object type constraints. Import ``PREDICATE_TYPE_PAIRS`` and ``ALLOWED_PREDICATES``
from here everywhere — never duplicate the vocabulary.
"""

from __future__ import annotations

import uuid

from doktok_core.knowledge_graph.resolve import KG_ENTITY_NAMESPACE, KG_KEY_SEP

# predicate -> allowed (subject_type, object_type) pairs.
# SINGLE SOURCE OF TRUTH: all provider extractors, the feature processor, and the circuit-breaker
# validator import from here.
PREDICATE_TYPE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "EMPLOYED_BY": [("PERSON", "ORG")],
    "BANKS_WITH": [("PERSON", "ORG")],
    "INSURED_BY": [("PERSON", "ORG")],
    "CUSTOMER_OF": [("PERSON", "ORG")],
    "CONTRACTS_WITH": [("PERSON", "ORG")],
    "REPRESENTED_BY": [("PERSON", "ORG"), ("PERSON", "PERSON")],
    "MEMBER_OF": [("PERSON", "ORG")],
    "RESIDES_IN": [("PERSON", "GPE"), ("PERSON", "LOCATION")],
    "LOCATED_IN": [("ORG", "GPE"), ("ORG", "LOCATION")],
    "RELATED_TO": [("PERSON", "PERSON")],
}

ALLOWED_PREDICATES: frozenset[str] = frozenset(PREDICATE_TYPE_PAIRS)


def canonical_edge_id(
    tenant_id: str, src_entity_id: str, predicate: str, dst_entity_id: str
) -> str:
    """Deterministic edge id: uuid5 of (tenant|src|predicate|dst) — same namespace discipline as
    ``canonical_entity_id``, so the same directed triple always maps to the same id."""
    key = (
        f"{tenant_id}{KG_KEY_SEP}{src_entity_id}{KG_KEY_SEP}{predicate}{KG_KEY_SEP}{dst_entity_id}"
    )
    return uuid.uuid5(KG_ENTITY_NAMESPACE, key).hex
