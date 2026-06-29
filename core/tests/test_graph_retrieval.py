"""KAG Phase 3 graph retrieval: the relational gate, deterministic entity linking, and the bounded
neighborhood / path traversal over the in-memory knowledge-graph repository."""

from __future__ import annotations

from doktok_contracts.schemas import (
    AliasFold,
    EntityType,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
)
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_core.knowledge_graph.retrieval import (
    DefaultGraphRetriever,
    link_entities,
    looks_relational,
)

TENANT = "t1"


def _eid(entity_type: EntityType, value: str) -> str:
    return canonical_entity_id(TENANT, entity_type.value, value)


def _node(entity_type: EntityType, value: str, tenant: str = TENANT) -> KgEntity:
    return KgEntity(
        id=canonical_entity_id(tenant, entity_type.value, value),
        tenant_id=tenant,
        entity_type=entity_type,
        normalized_value=value,
    )


def _edge(src: str, predicate: str, dst: str, tenant: str = TENANT) -> KgEdge:
    return KgEdge(
        id=canonical_edge_id(tenant, src, predicate, dst),
        tenant_id=tenant,
        src_entity_id=src,
        predicate=predicate,
        dst_entity_id=dst,
    )


def _prov(
    edge_id: str, doc: str, chunk: str, evidence: str, tenant: str = TENANT
) -> KgEdgeProvenance:
    return KgEdgeProvenance(
        id=f"p:{edge_id}:{doc}",
        tenant_id=tenant,
        edge_id=edge_id,
        document_id=doc,
        chunk_id=chunk,
        evidence=evidence,
    )


def _seed_graph() -> InMemoryKnowledgeGraphRepository:
    """alice -EMPLOYED_BY-> acme corp (doc1); acme corp -LOCATED_IN-> hamburg (doc2);
    bob -EMPLOYED_BY-> acme corp (doc3)."""
    repo = InMemoryKnowledgeGraphRepository()
    alice = _node(EntityType.PERSON, "alice")
    acme = _node(EntityType.ORG, "acme corp")
    hamburg = _node(EntityType.GPE, "hamburg")
    bob = _node(EntityType.PERSON, "bob")
    repo.upsert_entities([alice, acme, hamburg, bob])

    e1 = _edge(alice.id, "EMPLOYED_BY", acme.id)
    e2 = _edge(acme.id, "LOCATED_IN", hamburg.id)
    e3 = _edge(bob.id, "EMPLOYED_BY", acme.id)
    repo.replace_edges_for_document(
        TENANT, "doc1", [e1], [_prov(e1.id, "doc1", "c1", "Alice works at Acme Corp.")]
    )
    repo.replace_edges_for_document(
        TENANT, "doc2", [e2], [_prov(e2.id, "doc2", "c2", "Acme Corp is based in Hamburg.")]
    )
    repo.replace_edges_for_document(
        TENANT, "doc3", [e3], [_prov(e3.id, "doc3", "c3", "Bob is employed by Acme Corp.")]
    )
    return repo


# ----------------------------------------------------------------------- the gate


def test_looks_relational_fires_on_relational_cues() -> None:
    assert looks_relational("how is Alice connected to Acme Corp?")
    assert looks_relational("what is the relationship between Bob and Acme?")
    assert looks_relational("which people are associated with Acme Corp?")
    assert looks_relational("trace the chain from Alice to Hamburg")


def test_looks_relational_fires_on_household_relationship_cues() -> None:
    # The predicate vocabulary's real-world verbs are themselves the signal.
    assert looks_relational("Who is Johanna Mertens insured by?")
    assert looks_relational("Who does Stefan Vogel bank with?")
    assert looks_relational("Who is Stefan Vogel's employer?")


def test_looks_relational_skips_ordinary_questions() -> None:
    assert not looks_relational("what is the invoice total for March?")
    assert not looks_relational("summarize the contract")
    assert not looks_relational("when was this document signed?")


# ----------------------------------------------------------------------- entity linking


def test_link_entities_resolves_question_terms_to_nodes() -> None:
    repo = _seed_graph()
    linked = link_entities("how is alice connected to acme corp", repo, TENANT)
    ids = {n.id for n in linked}
    assert _eid(EntityType.PERSON, "alice") in ids
    assert _eid(EntityType.ORG, "acme corp") in ids
    # 'hamburg' / 'bob' are not named in the question
    assert _eid(EntityType.GPE, "hamburg") not in ids


def test_link_entities_prefers_longest_surface_form() -> None:
    repo = _seed_graph()
    # 'acme corp' must link the full org, not just match a shorter prefix.
    linked = link_entities("tell me about acme corp", repo, TENANT)
    assert [n.normalized_value for n in linked] == ["acme corp"]


def test_link_entities_matches_token_boundaries_only() -> None:
    repo = _seed_graph()
    # 'bobsled' must not match the PERSON 'bob' (no token boundary).
    assert link_entities("anything about bobsled racing?", repo, TENANT) == []


def test_link_entities_resolves_via_alias() -> None:
    repo = _seed_graph()
    acme_id = _eid(EntityType.ORG, "acme corp")
    alias = _node(EntityType.ORG, "acme")
    repo.upsert_entities([alias])
    repo.resolve_aliases(
        TENANT,
        [
            AliasFold(
                alias_id=alias.id, alias_type="ORG", alias_normalized="acme", canonical_id=acme_id
            )
        ],
    )
    # The bare 'acme' surface form now resolves to the canonical 'acme corp' node via the alias map.
    linked = link_entities("who is connected to acme", repo, TENANT)
    assert [n.id for n in linked] == [acme_id]


# ----------------------------------------------------------------------- traversal repo


def test_neighborhood_one_hop_is_bounded() -> None:
    repo = _seed_graph()
    alice = _eid(EntityType.PERSON, "alice")
    edges, prov = repo.neighborhood(TENANT, [alice], hops=1)
    # Only the alice<->acme edge: hamburg and bob are 2 hops away.
    assert len(edges) == 1
    assert edges[0].predicate == "EMPLOYED_BY"
    assert {p.document_id for p in prov} == {"doc1"}


def test_neighborhood_two_hops_reaches_further() -> None:
    repo = _seed_graph()
    alice = _eid(EntityType.PERSON, "alice")
    edges, _ = repo.neighborhood(TENANT, [alice], hops=2)
    # alice->acme, acme->hamburg, bob->acme all sit within 2 hops of alice.
    assert len(edges) == 3


def test_neighborhood_respects_edge_limit() -> None:
    repo = _seed_graph()
    acme = _eid(EntityType.ORG, "acme corp")
    edges, _ = repo.neighborhood(TENANT, [acme], hops=1, edge_limit=1)
    assert len(edges) == 1


def test_path_between_two_entities() -> None:
    repo = _seed_graph()
    alice = _eid(EntityType.PERSON, "alice")
    hamburg = _eid(EntityType.GPE, "hamburg")
    edges, prov = repo.path_between(TENANT, alice, hamburg, max_hops=2)
    preds = [e.predicate for e in edges]
    assert preds == ["EMPLOYED_BY", "LOCATED_IN"]
    assert {p.document_id for p in prov} == {"doc1", "doc2"}


def test_path_between_returns_empty_beyond_hop_bound() -> None:
    repo = _seed_graph()
    alice = _eid(EntityType.PERSON, "alice")
    hamburg = _eid(EntityType.GPE, "hamburg")
    # The shortest path is 2 hops; capping at 1 finds nothing.
    edges, prov = repo.path_between(TENANT, alice, hamburg, max_hops=1)
    assert edges == [] and prov == []


def test_traversal_is_cycle_safe() -> None:
    # A triangle alice->acme->bob->alice must terminate (visited guard), not loop forever.
    repo = InMemoryKnowledgeGraphRepository()
    a = _node(EntityType.PERSON, "alice")
    o = _node(EntityType.ORG, "acme corp")
    b = _node(EntityType.PERSON, "bob")
    repo.upsert_entities([a, o, b])
    e1 = _edge(a.id, "EMPLOYED_BY", o.id)
    e2 = _edge(o.id, "RELATED_TO", b.id)
    e3 = _edge(b.id, "RELATED_TO", a.id)
    repo.replace_edges_for_document(
        TENANT,
        "doc1",
        [e1, e2, e3],
        [
            _prov(e1.id, "doc1", "c1", "x"),
            _prov(e2.id, "doc1", "c2", "y"),
            _prov(e3.id, "doc1", "c3", "z"),
        ],
    )
    edges, _ = repo.neighborhood(TENANT, [a.id], hops=2)
    assert len(edges) == 3  # all three, no duplication / no hang
    path, _ = repo.path_between(TENANT, a.id, b.id, max_hops=2)
    assert len(path) == 1  # the direct bob<->alice edge is the shortest path


def test_traversal_tenant_isolation() -> None:
    repo = _seed_graph()
    # A different tenant sees no edges even with a valid-looking seed id.
    other = canonical_entity_id("t2", EntityType.PERSON.value, "alice")
    edges, prov = repo.neighborhood("t2", [other], hops=2)
    assert edges == [] and prov == []


# ----------------------------------------------------------------------- end-to-end retriever


def test_graph_retriever_path_for_two_entities() -> None:
    repo = _seed_graph()
    result = DefaultGraphRetriever(repo).retrieve(TENANT, "how is alice connected to hamburg")
    rels = {(t.subject, t.predicate, t.object) for t in result.triples}
    assert ("alice", "EMPLOYED_BY", "acme corp") in rels
    assert ("acme corp", "LOCATED_IN", "hamburg") in rels
    # Hits are chunk-grounded so they can fuse + cite like any retrieval result.
    assert {h.chunk_id for h in result.hits} == {"c1", "c2"}
    assert all(h.text for h in result.hits)


def test_graph_retriever_neighborhood_for_single_entity() -> None:
    repo = _seed_graph()
    result = DefaultGraphRetriever(repo, hops=1).retrieve(TENANT, "what relates to acme corp")
    # The 1-hop neighborhood of acme covers all three edges incident to it.
    assert len(result.triples) == 3
    assert {h.document_id for h in result.hits} == {"doc1", "doc2", "doc3"}


def test_graph_retriever_empty_when_nothing_links() -> None:
    repo = _seed_graph()
    result = DefaultGraphRetriever(repo).retrieve(TENANT, "how is the weather connected to lunch")
    assert result.hits == [] and result.triples == []


def test_graph_retriever_empty_on_empty_graph() -> None:
    repo = InMemoryKnowledgeGraphRepository()
    result = DefaultGraphRetriever(repo).retrieve(TENANT, "how is alice connected to acme corp")
    assert result.hits == [] and result.triples == []
