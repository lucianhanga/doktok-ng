"""LLM adjudication layer over deterministic entity merge suggestions (#510).

Takes the output of the deterministic cascade (``list_merge_suggestions``) and filters it through
an LLM adjudicator that judges whether fuzzy-matched pairs are truly the same real-world entity:

- ``token_set`` suggestions (score 1.0, word-order/punctuation variants) are SKIPPED - they are
  certain matches and require no LLM call.
- EVERY other method - ``fuzzy_trgm``, ``token_subset`` (#533), ``token_typo`` (#534), and any
  future stage label - is adjudicated: if the LLM says the pair represents DIFFERENT real-world
  entities, the suggestion is DROPPED; if SAME, it is kept and enriched with llm_* fields. The
  LLM's neighborhood context (each entity's direct KG edges) is the over-merge guard.
- If the adjudicator raises or is unavailable, all suggestions are returned unchanged (graceful
  fallback - never let a model error break the human-review merge queue).
- Only the first ``limit`` suggestions are adjudicated (bounding the LLM call count).
- NO auto-merge: the human still approves every merge via the existing queue.
"""

from __future__ import annotations

import logging

from doktok_contracts.ports import EntityMergeAdjudicator, KnowledgeGraphRepository
from doktok_contracts.schemas import EntityProfile, KgMergeSuggestion

from doktok_core.knowledge_graph.entity_resolution import METHOD_TOKEN_SET

logger = logging.getLogger("doktok.kg.adjudication")

# Number of direct KG edges to include per entity profile (disambiguation context).
_NEIGHBOR_TOP_K = 5


def build_entity_profile(
    tenant_id: str,
    entity_id: str,
    normalized_value: str,
    entity_type: str,
    kg: KnowledgeGraphRepository,
) -> EntityProfile:
    """Assemble a compact profile card for one entity: name + type + top-K neighbor edges.

    Each neighbor appears as ``"predicate neighbor_name (TYPE)"`` so the adjudicator can use
    relationship context to distinguish same-name-different-people cases (the key over-merge
    defence).  Returns an empty neighbor list when the entity has no KG edges yet.
    """
    edges = kg.edges_for_entity(tenant_id, entity_id)
    if not edges:
        return EntityProfile(
            entity_id=entity_id,
            entity_type=entity_type,
            normalized_value=normalized_value,
        )

    # Collect unique neighbor ids (over-fetch so we get _NEIGHBOR_TOP_K distinct neighbors).
    neighbor_ids: list[str] = []
    seen: set[str] = set()
    for edge in edges:
        other_id = edge.dst_entity_id if edge.src_entity_id == entity_id else edge.src_entity_id
        if other_id not in seen:
            seen.add(other_id)
            neighbor_ids.append(other_id)

    node_map = {n.id: n for n in kg.get_entities(tenant_id, neighbor_ids)}
    neighbors: list[str] = []
    for edge in edges[:_NEIGHBOR_TOP_K]:
        nbr_id = edge.dst_entity_id if edge.src_entity_id == entity_id else edge.src_entity_id
        nbr = node_map.get(nbr_id)
        if nbr:
            neighbors.append(f"{edge.predicate} {nbr.normalized_value} ({nbr.entity_type})")

    return EntityProfile(
        entity_id=entity_id,
        entity_type=entity_type,
        normalized_value=normalized_value,
        neighbors=neighbors,
    )


def adjudicate_suggestions(
    suggestions: list[KgMergeSuggestion],
    kg: KnowledgeGraphRepository,
    adjudicator: EntityMergeAdjudicator,
    *,
    limit: int = 50,
) -> list[KgMergeSuggestion]:
    """Apply the LLM adjudication layer to a deterministic suggestion list.

    Returns a new list that:
    - preserves ``token_set`` suggestions unchanged (no LLM call, they are certain),
    - adjudicates every OTHER method (``fuzzy_trgm``, ``token_subset``, ``token_typo``, ...):
      drops suggestions where the LLM says the entities differ,
    - enriches surviving adjudicated suggestions with ``llm_*`` fields,
    - possibly overrides canonical direction when the LLM prefers the alias as canonical,
    - falls back to the original suggestion (unchanged) on any adjudicator error.

    Only the first ``limit`` suggestions are processed; the human still approves all merges.
    """
    result: list[KgMergeSuggestion] = []

    for s in suggestions[:limit]:
        if s.method == METHOD_TOKEN_SET:
            # Certain match (identical token-sort keys) - skip LLM, pass through unchanged.
            result.append(s)
            continue

        # Any non-token_set method (fuzzy_trgm / token_subset / token_typo): adjudicate.
        try:
            profile_a = build_entity_profile(
                s.tenant_id, s.canonical_id, s.canonical_value, s.entity_type, kg
            )
            profile_b = build_entity_profile(
                s.tenant_id, s.alias_id, s.alias_value, s.entity_type, kg
            )
            verdict = adjudicator.adjudicate(profile_a, profile_b)
        except Exception:
            logger.warning(
                "adjudicator error for %s / %s; keeping suggestion unchanged",
                s.canonical_id,
                s.alias_id,
                exc_info=True,
            )
            result.append(s)
            continue

        if not verdict.same:
            # LLM says different real-world entities - drop the suggestion from the queue.
            logger.debug(
                "adjudicator dropped %s / %s (confidence=%.2f): %s",
                s.canonical_id,
                s.alias_id,
                verdict.confidence,
                verdict.reason,
            )
            continue

        # LLM confirms same entity: enrich with verdict fields.
        update: dict[str, object] = {
            "llm_same": True,
            "llm_confidence": verdict.confidence,
            "llm_reason": verdict.reason,
            "llm_canonical": verdict.canonical or None,
        }

        # If the LLM prefers the alias value as the canonical name, flip the direction so the
        # human reviewer sees the LLM-preferred entity on the canonical side.
        if verdict.canonical and verdict.canonical.strip().lower() == s.alias_value.strip().lower():
            update["canonical_id"] = s.alias_id
            update["canonical_value"] = s.alias_value
            update["alias_id"] = s.canonical_id
            update["alias_value"] = s.canonical_value

        result.append(s.model_copy(update=update))

    return result
