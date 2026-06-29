"""Pure unit tests for containment-based alias folding (compute_alias_folds), KAG alias tier.

These are the sibling project's real scenarios, decided without any database.
"""

from __future__ import annotations

from doktok_contracts.schemas import EntityType, KgEntity
from doktok_core.knowledge_graph.alias import compute_alias_folds
from doktok_core.knowledge_graph.resolve import canonical_entity_id

TENANT = "t1"


def _node(entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(
        id=canonical_entity_id(TENANT, entity_type.value, value),
        tenant_id=TENANT,
        entity_type=entity_type,
        normalized_value=value,
    )


def _fold_targets(nodes: list[KgEntity]) -> dict[str, str]:
    # alias normalized_value -> canonical node's normalized_value, for readable assertions
    by_id = {n.id: n for n in nodes}
    return {
        f.alias_normalized: by_id[f.canonical_id].normalized_value
        for f in compute_alias_folds(nodes)
    }


def test_mnet_folds_into_full_name_while_mnet_internet_stays_separate() -> None:
    nodes = [
        _node(EntityType.ORG, "M-net"),
        _node(EntityType.ORG, "M-net Telekommunikations GmbH"),
        _node(EntityType.ORG, "M-net Internet"),
    ]
    folds = _fold_targets(nodes)
    # 'M-net' folds into the unique-longest superset...
    assert folds.get("M-net") == "M-net Telekommunikations GmbH"
    # ...and 'M-net Internet' is neither folded away nor a fold target (it is not a prefix of the
    # GmbH node, and it has no superset of its own).
    assert "M-net Internet" not in folds
    assert "M-net Telekommunikations GmbH" not in folds  # the canonical is never an alias


def test_multilevel_prefix_chain_collapses_to_single_canonical() -> None:
    nodes = [
        _node(EntityType.ORG, "Finanzamt"),
        _node(EntityType.ORG, "Finanzamt München"),
        _node(EntityType.ORG, "Finanzamt München für Körperschaften"),
    ]
    folds = _fold_targets(nodes)
    # Both shorter forms fold into the single terminal node.
    assert folds.get("Finanzamt") == "Finanzamt München für Körperschaften"
    assert folds.get("Finanzamt München") == "Finanzamt München für Körperschaften"


def test_alias_prefixing_two_equal_length_candidates_is_not_merged() -> None:
    # 'Bank' is a prefix of two distinct supersets that tie at the longest length -> ambiguous.
    nodes = [
        _node(EntityType.ORG, "Bank"),
        _node(EntityType.ORG, "Bank of America"),
        _node(EntityType.ORG, "Bank of England"),
    ]
    folds = _fold_targets(nodes)
    assert "Bank" not in folds  # no unique longest -> left alone
    # The two specific banks have no superset, so they stay as-is too.
    assert folds == {}


def test_never_folds_across_entity_type() -> None:
    # Same prefix but different types: a PERSON 'Max' must not fold into an ORG 'Max Mara'.
    nodes = [
        _node(EntityType.PERSON, "Max"),
        _node(EntityType.ORG, "Max Mara"),
    ]
    assert compute_alias_folds(nodes) == []


def test_generic_short_prefix_guard() -> None:
    # A 2-char alias is below MIN_ALIAS_CHARS and must not fold even with a clear superset.
    nodes = [
        _node(EntityType.ORG, "ab"),
        _node(EntityType.ORG, "ab company gmbh"),
    ]
    assert compute_alias_folds(nodes) == []


def test_amtsgericht_folds_into_munich_branch() -> None:
    nodes = [
        _node(EntityType.ORG, "Amtsgericht"),
        _node(EntityType.ORG, "Amtsgericht München"),
    ]
    folds = _fold_targets(nodes)
    assert folds.get("Amtsgericht") == "Amtsgericht München"
