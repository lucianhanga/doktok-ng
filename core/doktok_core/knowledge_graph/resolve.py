"""Deterministic cross-document entity resolution (KAG Phase 1).

The canonical node id is a pure function of ``(tenant_id, entity_type, normalized_value)``, computed
as a uuid5 over that triple. So two documents mentioning the same normalized entity map to the SAME
node deterministically, with no cross-document clustering and no global state - resolution is
per-document-decomposable and idempotent, which is what lets it run as a reconciler processor.

DEFERRED (Phase 2): the pgvector-fuzzy tier that would merge surface variants ("IBM" / "I.B.M." /
"International Business Machines") via entity embeddings + cosine threshold + connected-components
clustering. It is intentionally NOT implemented here - Phase 1 is deterministic exact-key only.
``FUZZY_RESOLUTION_ENABLED`` is the off switch; flipping it on is a Phase-2 task, not a config knob.
"""

from __future__ import annotations

import uuid

from doktok_contracts.schemas import EntityType

# Stable namespace for KAG canonical-entity ids. NEVER change this: it is baked into every stored
# node id, so altering it would orphan the entire graph. Derived once from a fixed DNS-style label.
_KG_ENTITY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "knowledge-graph.entities.doktok")

# Unit separator: an in-band-safe delimiter between the key parts so values containing common
# punctuation cannot collide (e.g. "a|b" + "c" vs "a" + "b|c").
_SEP = "\x1f"

# Entity types that become canonical knowledge-graph nodes. CUSTOM_TOKEN (lexical keyword tokens,
# ~200/document) is excluded: those are search keywords, not real-world entities, and would bloat
# the node store without graph value. The deprecated regex types are excluded too (no longer
# extracted; see migration 0030). This leaves the genuine entities: PERSON/ORG/GPE/LOCATION/etc.
_EXCLUDED_NODE_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.CUSTOM_TOKEN,
        EntityType.DATE,
        EntityType.MONEY,
        EntityType.DOCUMENT_ID,
        EntityType.INVOICE_ID,
        EntityType.CONTRACT_ID,
    }
)

KG_NODE_TYPES: tuple[str, ...] = tuple(t.value for t in EntityType if t not in _EXCLUDED_NODE_TYPES)

# Phase-2 flag, deliberately off. See module docstring.
FUZZY_RESOLUTION_ENABLED = False


def canonical_entity_id(tenant_id: str, entity_type: str, normalized_value: str) -> str:
    """The deterministic canonical node id for a normalized entity (uuid5 hex).

    Pure function of the triple: identical inputs always yield the same id, across documents and
    across re-runs. ``tenant_id`` is part of the key, so tenants never share node ids.
    """
    key = f"{tenant_id}{_SEP}{entity_type}{_SEP}{normalized_value}"
    return uuid.uuid5(_KG_ENTITY_NAMESPACE, key).hex
