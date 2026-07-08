"""Tests for FeatureGroup definitions in catalog.py (ADR-0009).

Validates that the group definitions are internally consistent with FEATURE_CATALOG: every feature
name referenced in badge_members and reprocess_set must exist as a known catalog entry, so a rename
or removal of a feature processor surfaces here rather than silently breaking the group reprocess
endpoint at runtime.
"""

from __future__ import annotations

import pytest
from doktok_core.features.catalog import FEATURE_CATALOG, FEATURE_GROUPS, FEATURE_GROUPS_BY_ID

_CATALOG_NAMES: frozenset[str] = frozenset(spec.name for spec in FEATURE_CATALOG)


def test_exactly_two_groups() -> None:
    assert len(FEATURE_GROUPS) == 2
    assert {g.id for g in FEATURE_GROUPS} == {"entities", "knowledge_graph"}


def test_groups_by_id_mirrors_groups_list() -> None:
    assert set(FEATURE_GROUPS_BY_ID.keys()) == {g.id for g in FEATURE_GROUPS}
    for g in FEATURE_GROUPS:
        assert FEATURE_GROUPS_BY_ID[g.id] is g


@pytest.mark.parametrize("group", FEATURE_GROUPS)
def test_badge_members_in_catalog(group) -> None:  # type: ignore[no-untyped-def]
    for name in group.badge_members:
        assert name in _CATALOG_NAMES, (
            f"group {group.id!r}: badge member {name!r} is not in FEATURE_CATALOG"
        )


@pytest.mark.parametrize("group", FEATURE_GROUPS)
def test_reprocess_set_in_catalog(group) -> None:  # type: ignore[no-untyped-def]
    for name in group.reprocess_set:
        assert name in _CATALOG_NAMES, (
            f"group {group.id!r}: reprocess_set member {name!r} is not in FEATURE_CATALOG"
        )


def test_entities_group_structure() -> None:
    g = FEATURE_GROUPS_BY_ID["entities"]
    assert g.label == "Entities"
    assert set(g.badge_members) == {"entities", "ner"}
    # AUTO-CHAIN: re-extracting entities/ner invalidates entity_graph and relations.
    assert set(g.reprocess_set) == {"entities", "ner", "entity_graph", "relations"}


def test_knowledge_graph_group_structure() -> None:
    g = FEATURE_GROUPS_BY_ID["knowledge_graph"]
    assert g.label == "Knowledge graph"
    assert set(g.badge_members) == {"entity_graph", "relations"}
    # Only the graph tier is reset; entity extraction is left untouched.
    assert set(g.reprocess_set) == {"entity_graph", "relations"}


def test_entities_reprocess_set_is_superset_of_badge_members() -> None:
    """The entities group auto-chains: its reprocess_set must cover its badge_members and more."""
    g = FEATURE_GROUPS_BY_ID["entities"]
    assert set(g.badge_members).issubset(set(g.reprocess_set))
    assert set(g.reprocess_set) > set(g.badge_members)


def test_knowledge_graph_reprocess_set_equals_badge_members() -> None:
    """The KG group only resets what it shows: reprocess_set == badge_members."""
    g = FEATURE_GROUPS_BY_ID["knowledge_graph"]
    assert set(g.reprocess_set) == set(g.badge_members)
