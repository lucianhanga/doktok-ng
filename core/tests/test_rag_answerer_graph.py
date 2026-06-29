"""KAG Phase 3 fusion into the RAG answerer: a relational question surfaces a cross-document
relationship in the grounded, cited context; a non-relational question is byte-identical to today
(no graph call, no relationship block)."""

from __future__ import annotations

from doktok_contracts.schemas import (
    EntityType,
    GraphRetrieval,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    SearchHit,
)
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from doktok_core.knowledge_graph.retrieval import DefaultGraphRetriever
from doktok_core.rag.answerer import DefaultRagAnswerer

TENANT = "t1"


def _node(entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(
        id=canonical_entity_id(TENANT, entity_type.value, value),
        tenant_id=TENANT,
        entity_type=entity_type,
        normalized_value=value,
    )


def _edge(src: str, predicate: str, dst: str) -> KgEdge:
    return KgEdge(
        id=canonical_edge_id(TENANT, src, predicate, dst),
        tenant_id=TENANT,
        src_entity_id=src,
        predicate=predicate,
        dst_entity_id=dst,
    )


def _prov(edge_id: str, doc: str, chunk: str, evidence: str) -> KgEdgeProvenance:
    return KgEdgeProvenance(
        id=f"p:{edge_id}:{doc}",
        tenant_id=TENANT,
        edge_id=edge_id,
        document_id=doc,
        chunk_id=chunk,
        evidence=evidence,
    )


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]
        return list(self._hits)


class FakeChat:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        return self._reply


class RecordingGraphRetriever:
    """A graph retriever that records whether it was consulted (to prove the gate)."""

    def __init__(self) -> None:
        self.calls = 0

    def retrieve(self, tenant_id, question, *, limit=10):  # type: ignore[no-untyped-def]
        self.calls += 1
        return GraphRetrieval()


def _hybrid_hit() -> SearchHit:
    return SearchHit(
        document_id="dh",
        chunk_id="hybrid-1",
        original_filename="hybrid.txt",
        page_start=1,
        snippet="hybrid snippet",
        text="hybrid chunk text",
        score=0.03,
    )


def _two_entity_graph() -> InMemoryKnowledgeGraphRepository:
    repo = InMemoryKnowledgeGraphRepository()
    alice = _node(EntityType.PERSON, "alice")
    acme = _node(EntityType.ORG, "acme corp")
    repo.upsert_entities([alice, acme])
    e1 = _edge(alice.id, "EMPLOYED_BY", acme.id)
    repo.replace_edges_for_document(
        TENANT, "doc1", [e1], [_prov(e1.id, "doc1", "c1", "Alice works at Acme Corp.")]
    )
    return repo


def test_relational_question_fuses_graph_relationship_into_grounded_context() -> None:
    graph = DefaultGraphRetriever(_two_entity_graph())
    # The model cites [2] (the graph evidence chunk packed second after the hybrid hit).
    chat = FakeChat("Alice is employed by Acme Corp [2].")
    answerer = DefaultRagAnswerer(FakeRetriever([_hybrid_hit()]), chat, graph_retriever=graph)

    answer = answerer.answer_thread(TENANT, [], "how is alice connected to acme corp", 8)

    assert chat.prompt is not None
    # The relationship scaffold is injected and the underlying evidence chunk is in context.
    assert "Known relationships from the document knowledge graph" in chat.prompt
    assert "alice EMPLOYED_BY acme corp" in chat.prompt
    assert "Alice works at Acme Corp." in chat.prompt
    # The answer is grounded and cites the graph-evidence document (cross-document provenance).
    assert answer.grounded is True
    cited_docs = {c.document_id for c in answer.citations}
    assert "doc1" in cited_docs


def test_non_relational_question_does_not_consult_graph_or_change_prompt() -> None:
    recorder = RecordingGraphRetriever()
    chat = FakeChat("The total is 100 [1].")
    answerer = DefaultRagAnswerer(FakeRetriever([_hybrid_hit()]), chat, graph_retriever=recorder)

    answer = answerer.answer_thread(TENANT, [], "what is the invoice total?", 8)

    # Gate did not fire -> graph never consulted, no relationship block, behaviour unchanged.
    assert recorder.calls == 0
    assert chat.prompt is not None
    assert "Known relationships" not in chat.prompt
    assert answer.grounded is True


def test_graph_failure_degrades_to_hybrid_only() -> None:
    class Boom:
        def retrieve(self, tenant_id, question, *, limit=10):  # type: ignore[no-untyped-def]
            raise RuntimeError("graph down")

    chat = FakeChat("Answer from hybrid [1].")
    answerer = DefaultRagAnswerer(FakeRetriever([_hybrid_hit()]), chat, graph_retriever=Boom())
    # A relational question that explodes in graph retrieval still answers from the hybrid hit.
    answer = answerer.answer_thread(TENANT, [], "how is alice connected to acme corp", 8)
    assert answer.grounded is True
    assert chat.prompt is not None and "Known relationships" not in chat.prompt


def test_graph_only_evidence_answers_when_hybrid_is_empty() -> None:
    graph = DefaultGraphRetriever(_two_entity_graph())
    chat = FakeChat("Alice is employed by Acme Corp [1].")
    answerer = DefaultRagAnswerer(FakeRetriever([]), chat, graph_retriever=graph)
    answer = answerer.answer_thread(TENANT, [], "how is alice connected to acme corp", 8)
    # With no hybrid hits, the graph evidence alone grounds the answer instead of refusing.
    assert answer.grounded is True
    assert any(c.document_id == "doc1" for c in answer.citations)
