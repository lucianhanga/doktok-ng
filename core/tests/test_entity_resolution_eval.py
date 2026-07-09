"""Golden-set evaluation for the entity-resolution cascade (#508, Wave 1).

This is the labeled fixture referenced by ``doktok_core.knowledge_graph.entity_resolution``:
``SUGGESTION_THRESHOLD`` is tuned so that every pair in it lands on the right side. The set
contains one real person under six surface variants (word order, punctuation, no-space
concatenation, a typo, an extra middle name), a second person sharing the surname (with his own
middle-name subset variant, #533/#534 coverage), a bare single-token surname that must match
nothing, plus confusable NEGATIVE pairs that must NOT merge.

Methodology: mint one pre-#508 canonical node per surface form (distinct ids, so the token_set
stage has to do the collapsing that write-time keying would normally do), run
``MatchCascade.propose`` over all of them, take the transitive closure of the proposed pairs
(union-find - a merge relation is an equivalence), and score PAIRWISE precision/recall against
the labeled clusters. Precision must be exactly 1.0: a single over-merge poisons canonical
identity for every mention behind it. Recall must be 1.0 on this set: each variant class here is
one the deterministic cascade explicitly claims to catch.

Known P0 limitation (asserted below as documentation, not aspiration): two genuinely different
people who share the exact same name are INSEPARABLE by any name-only signal - write-time keying
collapses them into one node by construction. Splitting those requires context/embedding signals
(P1+), plus the manual ``split_entity`` escape hatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations

from doktok_contracts.schemas import EntityType, KgEntity
from doktok_core.entities.ner import normalize_entity_name
from doktok_core.knowledge_graph.entity_resolution import (
    METHOD_TOKEN_SET,
    METHOD_TOKEN_SUBSET,
    METHOD_TOKEN_TYPO,
    SUGGESTION_THRESHOLD,
    MatchCascade,
    trigram_similarity,
)

TENANT = "tenant-golden"

# The golden set: each inner tuple is ONE real-world entity (a cluster); singleton clusters are
# the negatives - people who must NOT be linked to anyone else in the set.
GOLDEN_CLUSTERS: tuple[tuple[str, ...], ...] = (
    # One real person, six surface variants seen across documents:
    #   word order + punctuation ('hanga,lucian'), an extra middle name (+ its order variant),
    #   a no-space concatenation ('lucianhanga'), and an OCR-ish typo ('hanja lucian').
    (
        "lucian hanga",
        "hanga,lucian",
        "hanga lucian cosmin",
        "lucian cosmin hanga",
        "lucianhanga",
        "hanja lucian",
    ),
    # A SECOND person sharing lucian's surname: 'daniel hanga' must NOT link to any lucian
    # variant (different given name - the token_subset stage excludes it by construction:
    # {daniel, hanga} is not a subset of {hanga, lucian}). His middle-name variant IS the same
    # person (token_subset positive, #533), making this cluster the shared-surname tripwire for
    # both directions at once: link within, never across.
    ("daniel hanga", "daniel dennis hanga"),
    # A bare single-token surname must match NOTHING: it is a proper subset of half this fixture,
    # which is exactly why the subset stage requires >= 2 tokens on the smaller side (#533).
    ("hanga",),
    # Confusable negative: same given name, near-identical surname - two DIFFERENT people. Also
    # the token_typo tripwire (#534): 'gruber'/'huber' differ in the LEADING character, which the
    # same-first-char guard treats as a different name, not an OCR typo.
    ("hans gruber",),
    ("hans huber",),
    # Two different people sharing a surname - must not link either.
    ("anna schmidt",),
    ("bernd schmidt",),
)


def _entities() -> tuple[list[KgEntity], dict[str, int]]:
    """One pre-#508 node per surface form; returns (nodes, node_id -> true cluster index)."""
    nodes: list[KgEntity] = []
    truth: dict[str, int] = {}
    for cluster_idx, cluster in enumerate(GOLDEN_CLUSTERS):
        for value_idx, value in enumerate(cluster):
            node_id = f"golden-{cluster_idx}-{value_idx}"
            nodes.append(
                KgEntity(
                    id=node_id,
                    tenant_id=TENANT,
                    entity_type=EntityType.PERSON,
                    normalized_value=value,
                )
            )
            truth[node_id] = cluster_idx
    return nodes, truth


def _connected_components(node_ids: list[str], links: set[tuple[str, str]]) -> dict[str, str]:
    """Union-find over the proposed merge pairs: node id -> component root."""
    parent = {nid: nid for nid in node_ids}

    def find(nid: str) -> str:
        while parent[nid] != nid:
            parent[nid] = parent[parent[nid]]
            nid = parent[nid]
        return nid

    for a, b in links:
        parent[find(a)] = find(b)
    return {nid: find(nid) for nid in node_ids}


def _pairs_within(assignment: Mapping[str, object]) -> set[frozenset[str]]:
    """All unordered same-group pairs implied by a node -> group assignment."""
    return {
        frozenset((a, b))
        for a, b in combinations(sorted(assignment), 2)
        if assignment[a] == assignment[b]
    }


def _fmt(pairs: set[frozenset[str]]) -> list[tuple[str, ...]]:
    return sorted(tuple(sorted(pair)) for pair in pairs)


def test_golden_set_pairwise_precision_and_recall() -> None:
    nodes, truth = _entities()
    suggestions = MatchCascade().propose(nodes)

    proposed_links = {(s.canonical_id, s.alias_id) for s in suggestions}
    components = _connected_components([n.id for n in nodes], proposed_links)

    predicted_pairs = _pairs_within(components)
    true_pairs = _pairs_within(truth)
    true_positives = predicted_pairs & true_pairs

    precision = len(true_positives) / len(predicted_pairs) if predicted_pairs else 1.0
    recall = len(true_positives) / len(true_pairs) if true_pairs else 1.0

    by_id = {n.id: n.normalized_value for n in nodes}
    print(
        f"\ngolden-set eval: {len(nodes)} nodes, {len(suggestions)} suggestions, "
        f"{len(predicted_pairs)} predicted pairs, {len(true_pairs)} true pairs"
    )
    print(f"pairwise precision = {precision:.3f}, pairwise recall = {recall:.3f}")
    for s in suggestions:
        print(f"  {s.method:>10} {s.score:.3f}  {s.alias_value!r} -> {s.canonical_value!r}")
    missed = true_pairs - predicted_pairs
    for a, b in _fmt(missed):
        print(f"  MISSED: {by_id[a]!r} <-> {by_id[b]!r}")

    # A single over-merge corrupts identity for every mention behind it: precision must be 1.0.
    assert precision == 1.0, f"over-merge: false pairs {_fmt(predicted_pairs - true_pairs)}"
    # Every variant class in the fixture is one the P0 cascade claims to catch.
    assert recall == 1.0, f"missed variant pairs: {_fmt(missed)}"


def test_all_six_variants_form_one_cluster() -> None:
    """The deliverable in one assertion: the six 'lucian hanga' variants are ONE entity."""
    nodes, truth = _entities()
    suggestions = MatchCascade().propose(nodes)
    components = _connected_components(
        [n.id for n in nodes], {(s.canonical_id, s.alias_id) for s in suggestions}
    )
    variant_ids = [nid for nid, cluster in truth.items() if cluster == 0]
    assert len(variant_ids) == 6
    assert len({components[nid] for nid in variant_ids}) == 1


def test_daniel_cluster_links_within_but_never_to_lucian() -> None:
    """#533 in one assertion: 'daniel hanga' folds into 'daniel dennis hanga' via token_subset,
    while the shared surname never links either daniel to any lucian variant."""
    nodes, truth = _entities()
    suggestions = MatchCascade().propose(nodes)
    components = _connected_components(
        [n.id for n in nodes], {(s.canonical_id, s.alias_id) for s in suggestions}
    )
    lucian_ids = {nid for nid, cluster in truth.items() if cluster == 0}
    daniel_ids = {nid for nid, cluster in truth.items() if cluster == 1}
    assert len({components[nid] for nid in daniel_ids}) == 1
    assert {components[nid] for nid in daniel_ids}.isdisjoint(
        {components[nid] for nid in lucian_ids}
    )
    # The link is labeled token_subset (a reviewable suggestion), directed into the longer name.
    daniel_links = [s for s in suggestions if {s.canonical_id, s.alias_id} == daniel_ids]
    assert [s.method for s in daniel_links] == [METHOD_TOKEN_SUBSET]
    assert daniel_links[0].canonical_value == "daniel dennis hanga"
    assert daniel_links[0].alias_value == "daniel hanga"


def test_single_token_hanga_matches_nothing() -> None:
    """The >= 2-token guard (#533): a bare surname appears in no suggestion at all."""
    nodes, truth = _entities()
    suggestions = MatchCascade().propose(nodes)
    (hanga_id,) = [nid for nid, cluster in truth.items() if cluster == 2]
    assert all(hanga_id not in (s.canonical_id, s.alias_id) for s in suggestions)


def test_typo_variant_links_via_token_typo() -> None:
    """#534: the OCR-ish 'hanja lucian' reaches its cluster through the token_typo stage."""
    nodes, _ = _entities()
    suggestions = MatchCascade().propose(nodes)
    typo_links = [s for s in suggestions if "hanja lucian" in (s.canonical_value, s.alias_value)]
    assert typo_links, "expected the typo variant to be linked"
    assert {s.method for s in typo_links} == {METHOD_TOKEN_TYPO}


def test_new_stage_methods_are_uncertain_suggestions() -> None:
    """token_subset / token_typo fire on this set and NEVER claim certainty: only token_set
    scores 1.0, so ``adjudicate_suggestions`` auto-routes every new-method pair to the LLM."""
    nodes, _ = _entities()
    suggestions = MatchCascade().propose(nodes)
    methods = {s.method for s in suggestions}
    assert METHOD_TOKEN_SUBSET in methods
    assert METHOD_TOKEN_TYPO in methods
    for s in suggestions:
        if s.method != METHOD_TOKEN_SET:
            assert s.score < 1.0, f"non-token_set method {s.method} must not claim certainty"


def test_negative_pairs_are_not_proposed() -> None:
    """'hans gruber' vs 'hans huber' (and the schmidts) stay below the suggestion threshold."""
    cascade = MatchCascade()

    def node(node_id: str, value: str) -> KgEntity:
        return KgEntity(
            id=node_id, tenant_id=TENANT, entity_type=EntityType.PERSON, normalized_value=value
        )

    gruber, huber = node("neg-1", "hans gruber"), node("neg-2", "hans huber")
    similarity = trigram_similarity(gruber.normalized_value, huber.normalized_value)
    print(f"\nnegative pair similarity: 'hans gruber' vs 'hans huber' = {similarity:.3f}")
    assert similarity < SUGGESTION_THRESHOLD
    assert cascade.score_pair(gruber, huber) is None
    assert cascade.propose([gruber, huber]) == []

    anna, bernd = node("neg-3", "anna schmidt"), node("neg-4", "bernd schmidt")
    assert cascade.score_pair(anna, bernd) is None
    assert cascade.propose([anna, bernd]) == []


def test_token_order_and_punctuation_variants_share_the_write_time_key() -> None:
    """Post-#508 these variants never even reach the matcher: they key to the same node."""
    assert normalize_entity_name("lucian hanga") == normalize_entity_name("hanga,lucian")
    assert normalize_entity_name("hanga lucian cosmin") == normalize_entity_name(
        "lucian cosmin hanga"
    )
    # The fuzzy-tier cases stay distinct keys on purpose (they need the trigram stage).
    assert normalize_entity_name("lucianhanga") != normalize_entity_name("lucian hanga")
    assert normalize_entity_name("hanja lucian") != normalize_entity_name("lucian hanga")


def test_known_limitation_same_name_different_people_collapse() -> None:
    """Documented P0 limitation: exact same-name people are one key - name-only cannot split
    them. This assertion pins the behavior so a future context-aware tier changes it knowingly."""
    assert normalize_entity_name("Hans Müller (the lawyer)") == normalize_entity_name(
        "hans müller, the lawyer"
    )
    assert normalize_entity_name("hans müller") == normalize_entity_name("Müller, Hans")
