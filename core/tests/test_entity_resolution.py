"""Unit tests for the deterministic entity-resolution cascade (#508, Wave 1).

Covers the pieces individually: the token-sort normalization, the pg_trgm-compatible trigram
implementation, all four cascade stages (token_set, token_subset #533, token_typo #534,
fuzzy_trgm), the comparability guards in ``score_pair``, canonical preference, and ``propose``
(blocking, canonical-only, ordering, limit). The end-to-end precision/recall numbers live in
``test_entity_resolution_eval``.
"""

from __future__ import annotations

import pytest
from doktok_contracts.schemas import EntityType, KgEntity
from doktok_core.entities.ner import normalize_entity_name
from doktok_core.knowledge_graph.entity_resolution import (
    METHOD_FUZZY_TRGM,
    METHOD_TOKEN_SET,
    METHOD_TOKEN_SUBSET,
    METHOD_TOKEN_TYPO,
    SUGGESTION_THRESHOLD,
    TOKEN_SUBSET_SCORE,
    TOKEN_TYPO_SCORE,
    MatchCascade,
    TokenSetStage,
    TokenSubsetStage,
    TokenTypoStage,
    TrigramStage,
    canonical_preference,
    is_canonical,
    is_typo_token_pair,
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


def test_token_subset_stage_fires_on_proper_subset_names() -> None:
    stage = TokenSubsetStage()
    assert stage.score("lucian hanga", "lucian cosmin hanga") == TOKEN_SUBSET_SCORE
    assert stage.score("lucian cosmin hanga", "lucian hanga") == TOKEN_SUBSET_SCORE  # symmetric
    # Word order and punctuation are already normalized away by the token vocabulary.
    assert stage.score("hanga,lucian", "hanga lucian cosmin") == TOKEN_SUBSET_SCORE


def test_token_subset_stage_requires_two_tokens_on_the_smaller_side() -> None:
    # A bare single-token surname is a proper subset of far too many names: must match nothing.
    stage = TokenSubsetStage()
    assert stage.score("hanga", "lucian hanga") is None
    assert stage.score("lucian hanga", "hanga") is None
    assert stage.score("hanga", "daniel dennis hanga") is None


def test_token_subset_stage_rejects_equal_and_non_subset_sets() -> None:
    stage = TokenSubsetStage()
    # Equal token sets are stage 1's certain tier, not a subset.
    assert stage.score("lucian hanga", "hanga lucian") is None
    # Shared surname with different given names is NOT a subset - excluded by construction.
    assert stage.score("lucian hanga", "daniel hanga") is None
    # All shared tokens must be EXACT: the typo'd variant is not a subset of the full name.
    assert stage.score("hanja lucian", "lucian cosmin hanga") is None
    # Concatenations are a different token, not a subset (the fuzzy tier's job).
    assert stage.score("lucianhanga", "lucian hanga") is None


def test_is_typo_token_pair_guards() -> None:
    assert is_typo_token_pair("hanja", "hanga")  # one substitution, same first char
    assert is_typo_token_pair("lucain", "lucian")  # one adjacent transposition
    assert is_typo_token_pair("hangaa", "hanga")  # one insertion
    assert not is_typo_token_pair("hanga", "hanga")  # identical is not a typo
    assert not is_typo_token_pair("gruber", "huber")  # different FIRST character (load-bearing)
    assert not is_typo_token_pair("hanga", "janga")  # DL=1 but the leading char differs
    assert not is_typo_token_pair("jo", "ja")  # too short to carry a typo signal
    assert not is_typo_token_pair("hanja", "lucian")  # more than one edit apart


def test_token_typo_stage_fires_on_exactly_one_typo_token_pair() -> None:
    stage = TokenTypoStage()
    # The OCR-ish golden pair: exact 'lucian', typo pair hanja~hanga (same first char).
    assert stage.score("hanja lucian", "lucian hanga") == TOKEN_TYPO_SCORE
    assert stage.score("lucian hanga", "hanja lucian") == TOKEN_TYPO_SCORE  # symmetric
    assert stage.score("hanja lucian", "hanga,lucian") == TOKEN_TYPO_SCORE  # punctuation variant


def test_token_typo_stage_first_char_guard_keeps_gruber_and_huber_apart() -> None:
    # 'hans' pairs exactly; 'gruber'~'huber' differs in the LEADING character - the guard that
    # separates different-surname people from OCR errors, which rarely corrupt the first char.
    stage = TokenTypoStage()
    assert stage.score("hans gruber", "hans huber") is None
    # Same shape with a true DL=1 first-char substitution: still rejected.
    assert stage.score("hans gruber", "hans kruber") is None


def test_token_typo_stage_rejects_everything_outside_the_one_typo_budget() -> None:
    stage = TokenTypoStage()
    # Zero non-exact pairs = identical token sets: stage 1's job, not a typo.
    assert stage.score("lucian hanga", "hanga lucian") is None
    # Two typo pairs exceed the AT MOST ONE non-exact budget.
    assert stage.score("hanja lucia", "hanga lucian") is None
    # Different token counts cannot align 1:1.
    assert stage.score("hanja", "lucian hanga") is None
    assert stage.score("hanja lucian", "lucian cosmin hanga") is None
    # Single-token names are too ambiguous for a typo-only signal.
    assert stage.score("hanga", "hanja") is None
    # The one non-exact pair must be a typo pair, not an arbitrary token swap.
    assert stage.score("daniel hanga", "lucian hanga") is None


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
    # The subset stage outranks the fuzzy tier for the middle-name variant...
    assert cascade.score_pair(
        _entity("a", "lucian hanga"), _entity("b", "lucian cosmin hanga")
    ) == (METHOD_TOKEN_SUBSET, TOKEN_SUBSET_SCORE)
    # ...and the typo stage outranks it for the single-typo variant.
    assert cascade.score_pair(_entity("a", "hanja lucian"), _entity("b", "lucian hanga")) == (
        METHOD_TOKEN_TYPO,
        TOKEN_TYPO_SCORE,
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
