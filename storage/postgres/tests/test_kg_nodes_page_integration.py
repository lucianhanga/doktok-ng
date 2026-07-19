"""Integration tests for the new PostgresKnowledgeGraphRepository traversal-API methods:
list_entities_page, get_entities, and entity_type_counts (KAG Phase 1 traversal API).

Runs against a real database; the ``db`` fixture skips automatically when none is reachable
and only ever touches ``test*`` tenants.
"""

from __future__ import annotations

from doktok_contracts.schemas import EntityType, KgEntity
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_storage_postgres import Database, PostgresKnowledgeGraphRepository

TENANT = "test-kg-nodes"
TENANT_OTHER = "test-kg-nodes-other"


def _eid(entity_type: EntityType, value: str, tenant: str = TENANT) -> str:
    return canonical_entity_id(tenant, entity_type.value, value)


def _node(entity_type: EntityType, value: str, tenant: str = TENANT) -> KgEntity:
    return KgEntity(
        id=_eid(entity_type, value, tenant),
        tenant_id=tenant,
        entity_type=entity_type,
        normalized_value=value,
    )


def _seed(db: Database) -> PostgresKnowledgeGraphRepository:
    """Insert 4 canonical nodes for TENANT: alice, bob (PERSON), acme corp (ORG), hamburg (GPE)."""
    kg = PostgresKnowledgeGraphRepository(db)
    kg.upsert_entities(
        [
            _node(EntityType.PERSON, "alice"),
            _node(EntityType.PERSON, "bob"),
            _node(EntityType.ORG, "acme corp"),
            _node(EntityType.GPE, "hamburg"),
        ]
    )
    return kg


# -------------------------------------------------- list_entities_page


def test_list_entities_page_all_ordered(db: Database) -> None:
    """Returns all nodes ordered by normalized_value ASC."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT)
    assert len(nodes) == 4
    values = [n.normalized_value for n in nodes]
    assert values == sorted(values)


def test_list_entities_page_type_filter(db: Database) -> None:
    """entity_type filter returns only nodes of that type."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT, entity_type=EntityType.PERSON)
    assert len(nodes) == 2
    assert all(n.entity_type == EntityType.PERSON for n in nodes)


def test_list_entities_page_query_substring(db: Database) -> None:
    """query performs a case-insensitive substring match on normalized_value."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT, query="corp")
    assert len(nodes) == 1
    assert nodes[0].normalized_value == "acme corp"


def test_list_entities_page_query_case_insensitive(db: Database) -> None:
    """query is matched case-insensitively."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT, query="ALICE")
    assert len(nodes) == 1
    assert nodes[0].normalized_value == "alice"


def test_list_entities_page_query_escapes_like_wildcards(db: Database) -> None:
    # F-38 (#650): LIKE wildcards in the query must match LITERALLY - '50%' matches only a value
    # containing the literal percent sign, not every value containing '50'.
    kg = PostgresKnowledgeGraphRepository(db)
    kg.upsert_entities(
        [
            _node(EntityType.ORG, "50% off sale"),
            _node(EntityType.ORG, "item 50"),
        ]
    )
    nodes = kg.list_entities_page(TENANT, query="50%")
    assert [n.normalized_value for n in nodes] == ["50% off sale"]


def test_list_entities_page_query_and_type_combined(db: Database) -> None:
    """entity_type and query filters compose via AND."""
    kg = _seed(db)
    # "alice" is a PERSON: matches both filters
    nodes = kg.list_entities_page(TENANT, entity_type=EntityType.PERSON, query="alice")
    assert len(nodes) == 1
    # "acme corp" is an ORG, not a PERSON: excluded by type filter
    nodes = kg.list_entities_page(TENANT, entity_type=EntityType.PERSON, query="corp")
    assert nodes == []


def test_list_entities_page_pagination(db: Database) -> None:
    """Pagination with LIMIT/OFFSET returns non-overlapping pages that together cover all rows."""
    kg = _seed(db)
    first = kg.list_entities_page(TENANT, limit=2, offset=0)
    second = kg.list_entities_page(TENANT, limit=2, offset=2)
    assert len(first) == 2
    assert len(second) == 2
    assert {n.id for n in first}.isdisjoint({n.id for n in second})


def test_list_entities_page_no_match(db: Database) -> None:
    """A query with no matching nodes returns an empty list."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT, query="definitely-not-present-xyz")
    assert nodes == []


def test_list_entities_page_tenant_isolated(db: Database) -> None:
    """Nodes from TENANT are not visible under a different tenant id."""
    kg = _seed(db)
    nodes = kg.list_entities_page(TENANT_OTHER)
    assert nodes == []


# -------------------------------------------------- get_entities


def test_get_entities_batch(db: Database) -> None:
    """Batch-fetches the requested nodes by id."""
    kg = _seed(db)
    alice_id = _eid(EntityType.PERSON, "alice")
    org_id = _eid(EntityType.ORG, "acme corp")
    nodes = kg.get_entities(TENANT, [alice_id, org_id])
    assert len(nodes) == 2
    ids = {n.id for n in nodes}
    assert alice_id in ids
    assert org_id in ids


def test_get_entities_empty_input(db: Database) -> None:
    """Returns an empty list for an empty id list (no DB round-trip)."""
    kg = _seed(db)
    assert kg.get_entities(TENANT, []) == []


def test_get_entities_unknown_id(db: Database) -> None:
    """Returns an empty list when none of the given ids exist."""
    kg = _seed(db)
    assert kg.get_entities(TENANT, ["nonexistent-id"]) == []


def test_get_entities_tenant_isolated(db: Database) -> None:
    """Cannot retrieve a node using the wrong tenant id."""
    kg = _seed(db)
    alice_id = _eid(EntityType.PERSON, "alice")
    assert kg.get_entities(TENANT_OTHER, [alice_id]) == []


# -------------------------------------------------- entity_type_counts


def test_entity_type_counts(db: Database) -> None:
    """Returns one EntityTypeCount per type present, with correct counts."""
    kg = _seed(db)
    counts = kg.entity_type_counts(TENANT)
    by_type = {c.entity_type: c.count for c in counts}
    assert by_type[EntityType.PERSON.value] == 2
    assert by_type[EntityType.ORG.value] == 1
    assert by_type[EntityType.GPE.value] == 1


def test_entity_type_counts_empty_tenant(db: Database) -> None:
    """Returns an empty list when the tenant has no nodes."""
    kg = PostgresKnowledgeGraphRepository(db)
    counts = kg.entity_type_counts("test-kg-nodes-empty-tenant")
    assert counts == []


def test_entity_type_counts_tenant_isolated(db: Database) -> None:
    """Counts are scoped to the queried tenant."""
    kg = _seed(db)
    # Insert a node for a second tenant
    kg.upsert_entities([_node(EntityType.PERSON, "carol", TENANT_OTHER)])
    main_counts = {c.entity_type: c.count for c in kg.entity_type_counts(TENANT)}
    other_counts = {c.entity_type: c.count for c in kg.entity_type_counts(TENANT_OTHER)}
    # Main tenant still has 2 PERSONs
    assert main_counts[EntityType.PERSON.value] == 2
    # Other tenant has 1 PERSON
    assert other_counts[EntityType.PERSON.value] == 1
    assert EntityType.ORG.value not in other_counts
