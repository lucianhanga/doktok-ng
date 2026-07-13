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
    # #528: links a place to its postal code(s). Direction: the PLACE is the subject -
    # "München HAS_POSTAL_CODE 80287" - so a city fans out to its many codes the same way an
    # ORG fans out over LOCATED_IN targets. Emitted ONLY deterministically from the NER
    # PLZ-place split (see DETERMINISTIC_PREDICATES below), never by the model extractors.
    "HAS_POSTAL_CODE": [("GPE", "POSTAL_CODE"), ("LOCATION", "POSTAL_CODE")],
}

ALLOWED_PREDICATES: frozenset[str] = frozenset(PREDICATE_TYPE_PAIRS)

# Predicates produced only by deterministic code paths with exact provenance (#528), kept at
# 100% precision by construction. Provider extractors must NOT offer these to the model
# (their prompt builders skip them), and the relation circuit-breaker drops any model-produced
# triple that claims one.
DETERMINISTIC_PREDICATES: frozenset[str] = frozenset({"HAS_POSTAL_CODE"})


def canonical_edge_id(
    tenant_id: str, src_entity_id: str, predicate: str, dst_entity_id: str
) -> str:
    """Deterministic edge id: uuid5 of (tenant|src|predicate|dst) — same namespace discipline as
    ``canonical_entity_id``, so the same directed triple always maps to the same id."""
    key = (
        f"{tenant_id}{KG_KEY_SEP}{src_entity_id}{KG_KEY_SEP}{predicate}{KG_KEY_SEP}{dst_entity_id}"
    )
    return uuid.uuid5(KG_ENTITY_NAMESPACE, key).hex


def family_pair_key(a: str, b: str) -> str:
    """Order-independent key for a shared-surname pair of entity ids (#532): a family link is
    symmetric, so ``a|b`` and ``b|a`` collapse to one key. Shared by the confirm/dismiss endpoints,
    both repositories, and the family-suggestion grouping so the format never diverges."""
    return "|".join(sorted((a, b)))
