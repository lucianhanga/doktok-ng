"""In-memory repository tests for the entity-resolution methods (#508, Wave 1).

Covers ``find_similar_entities``, ``merge_entities``, ``split_entity`` and
``list_merge_suggestions`` on ``InMemoryKnowledgeGraphRepository``, which mirrors the Postgres
adapter's SQL semantics (the Postgres integration tests skip without a database). The matching
math itself is covered in ``test_entity_resolution`` / ``test_entity_resolution_eval``.
"""

from __future__ import annotations

import uuid

from doktok_contracts.schemas import (
    EntityType,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgEntityMention,
)
from doktok_core.knowledge_graph.entity_resolution import METHOD_FUZZY_TRGM, METHOD_TOKEN_SET
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id

TENANT = "t1"


def _entity(
    node_id: str,
    value: str,
    *,
    tenant: str = TENANT,
    entity_type: EntityType = EntityType.PERSON,
) -> KgEntity:
    return KgEntity(id=node_id, tenant_id=tenant, entity_type=entity_type, normalized_value=value)


def _mention(document_id: str, entity: KgEntity) -> KgEntityMention:
    return KgEntityMention(
        mention_id=uuid.uuid4().hex,
        tenant_id=entity.tenant_id,
        canonical_entity_id=entity.id,
        document_id=document_id,
        entity_type=entity.entity_type,
        normalized_value=entity.normalized_value,
    )


def _add_edge(
    kg: InMemoryKnowledgeGraphRepository,
    document_id: str,
    src: str,
    predicate: str,
    dst: str,
) -> str:
    edge_id = canonical_edge_id(TENANT, src, predicate, dst)
    edge = KgEdge(
        id=edge_id, tenant_id=TENANT, src_entity_id=src, predicate=predicate, dst_entity_id=dst
    )
    prov = KgEdgeProvenance(
        id=uuid.uuid4().hex,
        tenant_id=TENANT,
        edge_id=edge_id,
        document_id=document_id,
        evidence=f"{src} {predicate} {dst}",
    )
    kg.replace_edges_for_document(TENANT, document_id, [edge], [prov])
    return edge_id


# ------------------------------------------------------------------ find_similar_entities


def test_find_similar_returns_same_type_siblings_above_threshold() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            _entity("full", "lucian hanga"),
            _entity("concat", "lucianhanga"),  # 0.667 to the probe
            _entity("other", "hans gruber"),  # far below any threshold
            _entity("org", "lucian hanga", entity_type=EntityType.ORG),  # type mismatch
            _entity("foreign", "lucian hanga", tenant="t2"),  # tenant mismatch
        ]
    )
    matches = kg.find_similar_entities(TENANT, EntityType.PERSON, "lucian hanga", threshold=0.6)
    assert [(m.entity.id, round(m.score, 3)) for m in matches] == [("full", 1.0), ("concat", 0.667)]

    # The default point-lookup threshold is stricter (0.7): the concatenation drops out.
    strict = kg.find_similar_entities(TENANT, EntityType.PERSON, "lucian hanga")
    assert [m.entity.id for m in strict] == ["full"]


def test_find_similar_excludes_alias_nodes_and_respects_limit() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            _entity("full", "lucian hanga"),
            _entity("concat", "lucianhanga"),
            _entity("typo", "hanja lucian"),  # 0.625 to the probe
        ]
    )
    assert kg.merge_entities(TENANT, "full", "concat", method=METHOD_FUZZY_TRGM, score=0.667)
    matches = kg.find_similar_entities(TENANT, EntityType.PERSON, "lucian hanga", threshold=0.6)
    assert [m.entity.id for m in matches] == ["full", "typo"]  # merged alias no longer surfaces

    capped = kg.find_similar_entities(
        TENANT, EntityType.PERSON, "lucian hanga", threshold=0.6, limit=1
    )
    assert [m.entity.id for m in capped] == ["full"]  # best-first, then capped


# ------------------------------------------------------------------ merge_entities


def test_merge_repoints_mentions_records_alias_and_logs() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    canonical = _entity("canon", "lucian cosmin hanga")
    alias = _entity("alias", "lucianhanga")
    kg.upsert_entities([canonical, alias])
    kg.replace_mentions_for_document(TENANT, "d1", [_mention("d1", alias)])

    assert kg.merge_entities(TENANT, "canon", "alias", method=METHOD_FUZZY_TRGM, score=0.667)

    # Mentions moved onto the canonical; the alias holds none.
    assert {m.document_id for m in kg.mentions_for_entity(TENANT, "canon")} == {"d1"}
    assert kg.mentions_for_entity(TENANT, "alias") == []
    # Identity resolves through canonical_id: reading the alias returns the canonical node.
    resolved = kg.get_entity(TENANT, "alias")
    assert resolved is not None and resolved.id == "canon"
    # The alias surface form is recorded, so re-ingestion resolves straight to the canonical.
    assert kg.alias_map(TENANT)[(EntityType.PERSON.value, "lucianhanga")] == "canon"
    # The alias node is kept (reversibility) but no longer counts or lists as an entity.
    assert kg.entity_count(TENANT) == 1
    assert [e.id for e in kg.list_entities(TENANT)] == ["canon"]
    # One merge-log row with the method/score/actor.
    (row,) = kg._merge_log
    assert row["action"] == "merge"
    assert row["canonical_id"] == "canon" and row["alias_id"] == "alias"
    assert row["method"] == METHOD_FUZZY_TRGM and row["score"] == 0.667
    assert row["actor"] == "system"


def test_merge_repoints_edges_and_combines_duplicate_evidence() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            _entity("canon", "lucian cosmin hanga"),
            _entity("alias", "lucianhanga"),
            _entity("acme", "acme corp", entity_type=EntityType.ORG),
        ]
    )
    # d1 saw the alias working at acme; d2 saw the canonical working at acme.
    _add_edge(kg, "d1", "alias", "works_at", "acme")
    survivor_id = _add_edge(kg, "d2", "canon", "works_at", "acme")
    assert kg.edge_count(TENANT) == 2

    assert kg.merge_entities(TENANT, "canon", "alias", method=METHOD_FUZZY_TRGM, score=0.667)

    # The alias edge folded into the canonical edge; evidence from BOTH documents survives.
    assert kg.edge_count(TENANT) == 1
    (edge,) = kg.edges_for_entity(TENANT, "canon")
    assert edge.id == survivor_id
    assert edge.src_entity_id == "canon" and edge.dst_entity_id == "acme"
    assert edge.evidence_count == 2
    assert kg.edges_for_entity(TENANT, "alias") == []


def test_merge_guards_and_idempotency() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            _entity("person", "lucian hanga"),
            _entity("concat", "lucianhanga"),
            _entity("org", "lucian hanga gmbh", entity_type=EntityType.ORG),
        ]
    )
    assert not kg.merge_entities(TENANT, "person", "org", method="manual")  # cross-type
    assert not kg.merge_entities(TENANT, "person", "missing", method="manual")  # unknown alias
    assert not kg.merge_entities(TENANT, "missing", "concat", method="manual")  # unknown canonical
    assert not kg.merge_entities(TENANT, "person", "person", method="manual")  # self-merge
    assert not kg.merge_entities("t2", "person", "concat", method="manual")  # wrong tenant
    assert kg._merge_log == []

    assert kg.merge_entities(TENANT, "person", "concat", method=METHOD_TOKEN_SET, score=1.0)
    # Re-merging the already-merged pair re-asserts the state but logs nothing new.
    assert not kg.merge_entities(TENANT, "person", "concat", method=METHOD_TOKEN_SET, score=1.0)
    assert len(kg._merge_log) == 1


def test_merge_flattens_chains_to_the_canonical_root() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [_entity("a", "lucian cosmin hanga"), _entity("b", "lucian hanga"), _entity("c", "hanga")]
    )
    assert kg.merge_entities(TENANT, "a", "b", method="manual")
    # Merging INTO an alias resolves to its root: c ends up pointing at a, not b.
    assert kg.merge_entities(TENANT, "b", "c", method="manual")
    assert kg._entities["c"].canonical_id == "a"
    resolved = kg.get_entity(TENANT, "c")
    assert resolved is not None and resolved.id == "a"
    # Merging a whole cluster under a NEW canonical re-points the cluster's alias nodes too.
    kg.upsert_entities([_entity("root", "dr lucian cosmin hanga")])
    assert kg.merge_entities(TENANT, "root", "a", method="manual")
    assert kg._entities["b"].canonical_id == "root"
    assert kg._entities["c"].canonical_id == "root"
    assert kg.entity_count(TENANT) == 1


def test_merge_cannot_create_a_cycle() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities([_entity("a", "lucian cosmin hanga"), _entity("b", "lucian hanga")])
    assert kg.merge_entities(TENANT, "a", "b", method="manual")
    # The reverse merge resolves b to its root (a) and becomes a self-merge no-op.
    assert not kg.merge_entities(TENANT, "b", "a", method="manual")
    root = kg.get_entity(TENANT, "b")
    assert root is not None and root.id == "a"


# ------------------------------------------------------------------ split_entity


def test_split_reverses_a_merge() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    canonical = _entity("canon", "lucian cosmin hanga")
    alias = _entity("alias", "lucianhanga")
    kg.upsert_entities([canonical, alias])
    kg.replace_mentions_for_document(TENANT, "d1", [_mention("d1", alias)])
    assert kg.merge_entities(TENANT, "canon", "alias", method=METHOD_FUZZY_TRGM, score=0.667)
    assert kg.entity_count(TENANT) == 1

    assert kg.split_entity(TENANT, "alias", actor="reviewer")

    # The node is its own canonical again and counts/lists as an entity.
    restored = kg.get_entity(TENANT, "alias")
    assert restored is not None and restored.id == "alias" and restored.canonical_id is None
    assert kg.entity_count(TENANT) == 2
    # The alias mapping is gone: future ingests resolve the surface back to its own node.
    assert (EntityType.PERSON.value, "lucianhanga") not in kg.alias_map(TENANT)
    # Mentions are NOT walked back by split (port contract): the next KG feature reprocess
    # re-derives them onto the restored node via the deterministic ids.
    assert {m.document_id for m in kg.mentions_for_entity(TENANT, "canon")} == {"d1"}
    # A split audit row follows the merge row.
    assert [row["action"] for row in kg._merge_log] == ["merge", "split"]
    assert kg._merge_log[-1]["alias_id"] == "alias"
    assert kg._merge_log[-1]["actor"] == "reviewer"


def test_split_is_a_noop_for_canonical_or_missing_nodes() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities([_entity("canon", "lucian hanga")])
    assert not kg.split_entity(TENANT, "canon")  # already canonical
    assert not kg.split_entity(TENANT, "missing")
    assert not kg.split_entity("t2", "canon")  # wrong tenant
    assert kg._merge_log == []


def test_split_then_remerge_round_trips() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities([_entity("canon", "lucian cosmin hanga"), _entity("alias", "lucianhanga")])
    assert kg.merge_entities(TENANT, "canon", "alias", method=METHOD_FUZZY_TRGM, score=0.667)
    assert kg.split_entity(TENANT, "alias")
    assert kg.merge_entities(TENANT, "canon", "alias", method="manual")  # a REAL merge again
    assert [row["action"] for row in kg._merge_log] == ["merge", "split", "merge"]
    assert kg.entity_count(TENANT) == 1


# ------------------------------------------------------------------ list_merge_suggestions


def test_list_merge_suggestions_proposes_over_canonicals_only() -> None:
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            _entity("n1", "lucian hanga"),
            _entity("n2", "hanga,lucian"),  # pre-#508 order/punct variant: token_set
            _entity("n3", "lucianhanga"),  # fuzzy 0.667
            _entity("n4", "hans gruber"),
            _entity("n5", "hans huber"),  # 0.571: below the suggestion threshold
        ]
    )
    suggestions = kg.list_merge_suggestions(TENANT)
    assert suggestions, "expected merge suggestions for the variant nodes"
    assert suggestions[0].method == METHOD_TOKEN_SET and suggestions[0].score == 1.0
    suggested_ids = {s.alias_id for s in suggestions} | {s.canonical_id for s in suggestions}
    assert suggested_ids == {"n1", "n2", "n3"}  # the negatives never appear

    # A stricter threshold drops the fuzzy tier but keeps the exact token-set pair.
    strict = kg.list_merge_suggestions(TENANT, threshold=0.7)
    assert {s.method for s in strict} == {METHOD_TOKEN_SET}

    # Applying the best suggestion removes the pair from the next round.
    best = suggestions[0]
    assert kg.merge_entities(
        TENANT, best.canonical_id, best.alias_id, method=best.method, score=best.score
    )
    merged_pair = {best.canonical_id, best.alias_id}
    remaining = kg.list_merge_suggestions(TENANT)
    assert all({s.canonical_id, s.alias_id} != merged_pair for s in remaining)
