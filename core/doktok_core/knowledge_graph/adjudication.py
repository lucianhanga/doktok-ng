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
from doktok_contracts.schemas import (
    EntityProfile,
    KgAdjudicationVerdict,
    KgMergeSuggestion,
    MergeVerdict,
)

from doktok_core.knowledge_graph.entity_resolution import (
    METHOD_TOKEN_SET,
    merge_adjudication_pair_key,
    merge_adjudication_score_bucket,
)

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


def _cache_key(s: KgMergeSuggestion) -> tuple[str, str, str]:
    """The ``(pair_key, method, score_bucket)`` cache key for a suggestion (#535).

    ``pair_key`` is order-independent and normalized, so it survives a KG rebuild that re-mints the
    suggestion rows with fresh node ids (the same real-world pair keys the same cache row).
    """
    return (
        merge_adjudication_pair_key(s.canonical_value, s.alias_value),
        s.method,
        merge_adjudication_score_bucket(s.score),
    )


def _apply_verdict(
    s: KgMergeSuggestion, *, same: bool, confidence: float, reason: str, canonical: str | None
) -> KgMergeSuggestion | None:
    """Apply an adjudication verdict to a suggestion.

    Returns ``None`` when the verdict says the pair is DIFFERENT (the suggestion is dropped), or the
    enriched suggestion (``llm_*`` fields populated, canonical direction possibly flipped) when the
    verdict says SAME. Shared by the cache-hit and freshly-adjudicated paths so both apply identical
    logic.
    """
    if not same:
        logger.debug(
            "adjudicator dropped %s / %s (confidence=%.2f): %s",
            s.canonical_id,
            s.alias_id,
            confidence,
            reason,
        )
        return None

    update: dict[str, object] = {
        "llm_same": True,
        "llm_confidence": confidence,
        "llm_reason": reason,
        "llm_canonical": canonical or None,
    }
    # If the LLM prefers the alias value as the canonical name, flip the direction so the human
    # reviewer sees the LLM-preferred entity on the canonical side.
    if canonical and canonical.strip().lower() == s.alias_value.strip().lower():
        update["canonical_id"] = s.alias_id
        update["canonical_value"] = s.alias_value
        update["alias_id"] = s.canonical_id
        update["alias_value"] = s.canonical_value
    return s.model_copy(update=update)


def adjudicate_suggestions(
    suggestions: list[KgMergeSuggestion],
    kg: KnowledgeGraphRepository,
    adjudicator: EntityMergeAdjudicator,
    *,
    limit: int = 50,
) -> list[KgMergeSuggestion]:
    """Apply the LLM adjudication layer to a deterministic suggestion list, with a verdict cache.

    Returns a new list that:
    - preserves ``token_set`` suggestions unchanged (no LLM call, they are certain),
    - adjudicates every OTHER method (``fuzzy_trgm``, ``token_subset``, ``token_typo``, ...):
      drops suggestions where the LLM says the entities differ,
    - enriches surviving adjudicated suggestions with ``llm_*`` fields,
    - possibly overrides canonical direction when the LLM prefers the alias as canonical,
    - falls back to the original suggestion (unchanged) on any adjudicator error.

    Each non-``token_set`` pair is adjudicated ONCE and the verdict is CACHED (#535): a cache hit
    applies the stored verdict WITHOUT an LLM call, so a repeat call over unchanged candidates makes
    ZERO model calls. Cache misses call the adjudicator as before, then persist the verdict. The
    cache key is the normalized, order-independent entity pair (see ``merge_adjudication_pair_key``)
    plus method + rounded score, so the verdict survives a KG rebuild.

    Only the first ``limit`` suggestions are processed; the human still approves all merges.

    (#530, not built here: a future per-pair REJECTION store would be consulted before the cache
    lookup - it keys on the same normalized pair, so it composes cleanly on top of this path.)
    """
    batch = suggestions[:limit]

    # Batch-read cached verdicts for every non-token_set pair up front (one repository round-trip),
    # so a fully-cached repeat call issues no per-pair reads and no LLM calls at all.
    cache_keys = [_cache_key(s) for s in batch if s.method != METHOD_TOKEN_SET]
    try:
        cached = kg.get_cached_verdicts(batch[0].tenant_id, cache_keys) if cache_keys else {}
    except Exception:
        # A cache read failure must never break the queue: fall back to adjudicating every pair.
        logger.warning("adjudication cache read failed; adjudicating all pairs", exc_info=True)
        cached = {}

    result: list[KgMergeSuggestion] = []

    for s in batch:
        if s.method == METHOD_TOKEN_SET:
            # Certain match (identical token-sort keys) - skip LLM + cache, pass through unchanged.
            result.append(s)
            continue

        key = _cache_key(s)
        hit = cached.get(key)
        if hit is not None:
            # Cache hit: apply the stored verdict with NO LLM call (drop if different, enrich if
            # same). This is the zero-LLM path for repeat requests over unchanged candidates.
            applied = _apply_verdict(
                s,
                same=hit.same,
                confidence=hit.confidence,
                reason=hit.reason,
                canonical=hit.canonical,
            )
            if applied is not None:
                result.append(applied)
            continue

        # Cache miss: adjudicate via the LLM, then persist the verdict for next time.
        try:
            profile_a = build_entity_profile(
                s.tenant_id, s.canonical_id, s.canonical_value, s.entity_type, kg
            )
            profile_b = build_entity_profile(
                s.tenant_id, s.alias_id, s.alias_value, s.entity_type, kg
            )
            verdict: MergeVerdict = adjudicator.adjudicate(profile_a, profile_b)
        except Exception:
            # A single pair's adjudicator error must not abort the batch: keep it unchanged and do
            # NOT cache (so a transient model error is retried on the next request).
            logger.warning(
                "adjudicator error for %s / %s; keeping suggestion unchanged",
                s.canonical_id,
                s.alias_id,
                exc_info=True,
            )
            result.append(s)
            continue

        # Persist the verdict (best-effort: a cache write failure must not break the queue).
        pair_key, method, score_bucket = key
        try:
            kg.put_cached_verdict(
                s.tenant_id,
                pair_key=pair_key,
                method=method,
                score_bucket=score_bucket,
                verdict=KgAdjudicationVerdict(
                    same=verdict.same,
                    canonical=verdict.canonical or None,
                    confidence=verdict.confidence,
                    reason=verdict.reason,
                ),
            )
        except Exception:
            logger.warning(
                "adjudication cache write failed for %s / %s",
                s.canonical_id,
                s.alias_id,
                exc_info=True,
            )

        applied = _apply_verdict(
            s,
            same=verdict.same,
            confidence=verdict.confidence,
            reason=verdict.reason,
            canonical=verdict.canonical,
        )
        if applied is not None:
            result.append(applied)

    return result
