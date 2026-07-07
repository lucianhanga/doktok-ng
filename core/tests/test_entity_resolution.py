"""Unit tests for the deterministic entity-resolution cascade (#508, Wave 1).

Covers the pieces individually: the token-sort normalization, the pg_trgm-compatible trigram
implementation, both cascade stages, the comparability guards in ``score_pair``, canonical
preference, and ``propose`` (blocking, canonical-only, ordering, limit). The end-to-end
precision/recall numbers live in ``test_entity_resolution_eval``.
"""

from __future__ import annotations

import pytest
from doktok_contracts.schemas import EntityType, KgEntity
from doktok_core.entities.ner import normalize_entity_name
from doktok_core.knowledge_graph.entity_resolution import (
    METHOD_FUZZY_TRGM,
    METHOD_TOKEN_SET,
    SUGGESTION_THRESHOLD,
    MatchCascade,
    TokenSetStage,
    TrigramStage,
    canonical_preference,
    is_canonical,
    trigram_set,
    trigram_similarity,
)


def _entity(
    node_id: str,
    value: str,
    *,
    tenant: str = "t1",
    entity_type: EntityType = EntityType.PERSON,
    canonical_id: str | None = None,
) -> KgEntity:
    return KgEntity(
        id=node_id,
        tenant_id=tenant,
        entity_type=entity_type,
        normalized_value=value,
        canonical_id=canonical_id,
    )


# ------------------------------------------------------------------ normalize_entity_name


def test_normalize_sorts_tokens_and_strips_punctuation() -> None:
    assert normalize_entity_name("hanga,lucian") == "hanga lucian"
    assert normalize_entity_name("Lucian Hanga") == "hanga lucian"
    assert normalize_entity_name("hanga,lucian") == normalize_entity_name("lucian hanga")
    assert normalize_entity_name("  Hanga ,  Lucian ") == "hanga lucian"


def test_normalize_dedupes_tokens() -> None:
    assert normalize_entity_name("hanga hanga lucian") == "hanga lucian"


def test_normalize_all_punctuation_falls_back_to_ner_key() -> None:
    # An all-punctuation value must not collapse into the empty key.
    assert normalize_entity_name("!!!") == "!!!"
    assert normalize_entity_name("...") != normalize_entity_name("!!!")


# ------------------------------------------------------------------ trigram primitives


def test_trigram_set_matches_pg_trgm_word_padding() -> None:
    # pg_trgm: two leading spaces, one trailing, per word.
    assert trigram_set("hans") == frozenset({"  h", " ha", "han", "ans", "ns "})


def test_trigram_similarity_is_word_order_insensitive() -> None:
    assert trigram_similarity("lucian hanga", "hanga lucian") == 1.0


def test_trigram_similarity_empty_input_scores_zero() -> None:
    assert trigram_similarity("", "lucian") == 0.0
    assert trigram_similarity("lucian", "") == 0.0


def test_trigram_similarity_golden_values() -> None:
    # The values the threshold was tuned on (see the entity_resolution module docstring).
    assert trigram_similarity("lucian hanga", "lucianhanga") == pytest.approx(10 / 15)
    assert trigram_similarity("lucian hanga", "hanja lucian") == pytest.approx(10 / 16)
    assert trigram_similarity("lucian hanga", "lucian cosmin hanga") == pytest.approx(13 / 20)
    assert trigram_similarity("hans gruber", "hans huber") == pytest.approx(8 / 14)
    assert trigram_similarity("hans gruber", "hans huber") < SUGGESTION_THRESHOLD


# ------------------------------------------------------------------ stages


def test_token_set_stage_fires_on_order_and_punctuation_variants() -> None:
    stage = TokenSetStage()
    assert stage.score("hanga,lucian", "Lucian Hanga") == 1.0
    assert stage.score("lucian cosmin hanga", "hanga lucian cosmin") == 1.0
    assert stage.score("lucian hanga", "lucianhanga") is None  # concatenation is the fuzzy tier


def test_trigram_stage_fires_at_or_above_threshold_only() -> None:
    stage = TrigramStage()
    assert stage.score("lucian hanga", "lucianhanga") == pytest.approx(10 / 15)
    assert stage.score("lucian hanga", "hanja lucian") == pytest.approx(10 / 16)
    assert stage.score("hans gruber", "hans huber") is None  # 0.571 < 0.6


# ------------------------------------------------------------------ score_pair guards


def test_score_pair_is_none_across_tenants_types_and_for_identical_ids() -> None:
    cascade = MatchCascade()
    a = _entity("a", "lucian hanga")
    assert cascade.score_pair(a, _entity("b", "lucian hanga", tenant="t2")) is None
    assert cascade.score_pair(a, _entity("b", "lucian hanga", entity_type=EntityType.ORG)) is None
    assert cascade.score_pair(a, _entity("a", "lucian hanga")) is None


def test_score_pair_first_firing_stage_labels_the_pair() -> None:
    cascade = MatchCascade()
    assert cascade.score_pair(_entity("a", "lucian hanga"), _entity("b", "hanga,lucian")) == (
        METHOD_TOKEN_SET,
        1.0,
    )
    decision = cascade.score_pair(_entity("a", "lucian hanga"), _entity("b", "lucianhanga"))
    assert decision is not None
    method, score = decision
    assert method == METHOD_FUZZY_TRGM
    assert score == pytest.approx(10 / 15)
    assert cascade.score_pair(_entity("a", "hans gruber"), _entity("b", "hans huber")) is None


# ------------------------------------------------------------------ canonical preference


def test_is_canonical() -> None:
    assert is_canonical(_entity("a", "x"))
    assert is_canonical(_entity("a", "x", canonical_id="a"))
    assert not is_canonical(_entity("a", "x", canonical_id="b"))


def test_canonical_preference_prefers_more_tokens_then_length_then_smaller_id() -> None:
    short = _entity("z-short", "lucian hanga")
    long = _entity("a-long", "lucian cosmin hanga")
    assert canonical_preference(short, long) == (long, short)  # more tokens wins, order-agnostic
    assert canonical_preference(long, short) == (long, short)

    brief = _entity("a-brief", "hanga")
    full = _entity("z-full", "lucianhanga")
    assert canonical_preference(brief, full) == (full, brief)  # same tokens: longer value wins

    one = _entity("id-1", "lucian hanga")
    two = _entity("id-2", "hanga,lucian")
    assert canonical_preference(two, one) == (one, two)  # full tie: smaller id wins


# ------------------------------------------------------------------ propose


def test_propose_blocks_by_entity_type() -> None:
    entities = [
        _entity("a", "lucian hanga", entity_type=EntityType.PERSON),
        _entity("b", "hanga,lucian", entity_type=EntityType.ORG),
    ]
    assert MatchCascade().propose(entities) == []


def test_propose_only_considers_canonical_nodes() -> None:
    entities = [
        _entity("a", "lucian hanga"),
        _entity("b", "hanga,lucian", canonical_id="a"),  # already merged: not a candidate
    ]
    assert MatchCascade().propose(entities) == []


def test_propose_sorts_best_first_and_caps_at_limit() -> None:
    entities = [
        _entity("a", "lucian hanga"),
        _entity("b", "hanga,lucian"),  # token_set 1.0 with a
        _entity("c", "lucianhanga"),  # fuzzy 0.667 with a and with b
    ]
    suggestions = MatchCascade().propose(entities)
    assert len(suggestions) == 3
    assert [s.method for s in suggestions] == [
        METHOD_TOKEN_SET,
        METHOD_FUZZY_TRGM,
        METHOD_FUZZY_TRGM,
    ]
    assert suggestions[0].score == 1.0
    scores = [s.score for s in suggestions]
    assert scores == sorted(scores, reverse=True)

    capped = MatchCascade().propose(entities, limit=1)
    assert len(capped) == 1
    assert capped[0].method == METHOD_TOKEN_SET  # the cap keeps the best-scoring suggestion


def test_propose_direction_follows_canonical_preference() -> None:
    suggestions = MatchCascade().propose(
        [_entity("a", "lucian cosmin hanga"), _entity("b", "lucian hanga")]
    )
    assert len(suggestions) == 1
    assert suggestions[0].canonical_value == "lucian cosmin hanga"
    assert suggestions[0].alias_value == "lucian hanga"
