"""Gated, additive graph retrieval fused into hybrid RAG (KAG Phase 3).

This is the query-time half of KAG Path A. It mirrors the aggregation router's shape (a cheap
deterministic gate before any heavy work) and stays at the deterministic end of the spectrum - NO
LLM, NO agent loop (ADR-0018):

  * ``looks_relational`` - a conservative regex gate for relational / multi-hop questions ("how is X
    connected to Y", "what relates to Z"). Like ``looks_like_aggregation`` it only ever *adds* a
    path; a false positive is harmless because linking + traversal simply find nothing.
  * ``link_entities`` - deterministic dictionary linking: the question's terms are matched (token
    -boundary, longest-first) against the tenant's existing canonical nodes and folded aliases. No
    model call - the known entity surface forms ARE the vocabulary.
  * ``DefaultGraphRetriever`` - link -> traverse (A<->B path when two entities are named, else a
    bounded neighborhood of the seeds) -> chunk-grounded ``SearchHit``s + grounded relationship
    triples. The hits carry the edge's provenance chunk so they rerank + cite like any retrieval
    result, preserving the citation/refusal guarantees.

Local-mode only (entity-centric neighborhood + short paths). GraphRAG "global"/community-summary
retrieval and any bounded agentic multi-hop loop are deliberately out of scope for v1.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence

from doktok_contracts.ports import DocumentRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import (
    GraphRetrieval,
    GraphTriple,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    SearchHit,
)

from doktok_core.entities.ner import normalize_ner_name

logger = logging.getLogger("doktok.kag.retrieval")

# Cheap gate (no model) so ordinary semantic questions never touch the graph path. Conservative:
# the real guard is entity linking, so over-matching here is safe (it just adds candidates that the
# reranker can demote). Mirrors aggregation.router.looks_like_aggregation.
_REL_HINTS = re.compile(
    r"\b("
    r"connected|connection|connect|related|relate|relates|relationship|relationships|"
    r"linked|link|links|associated|association|associate|"
    r"between|both|together|tied to|involved with|involved in|"
    r"works? with|work with|deal(s)? with|relation(s)?|"
    r"trace|chain|path from|path between|how (is|are|does)"
    r")\b",
    re.IGNORECASE,
)

_RRF_K = 60  # match the hybrid retriever so graph hit scores are comparable in the candidate pool
_SNIPPET_CHARS = 240
_MIN_LINK_CHARS = 3  # ignore trivially short surface forms (matches the alias tier's guard)


def looks_relational(question: str) -> bool:
    """True if the question even might be relational/multi-hop - the gate before graph retrieval."""
    return bool(_REL_HINTS.search(question))


def _snippet(text: str) -> str:
    text = " ".join((text or "").split())
    return text[:_SNIPPET_CHARS] + ("..." if len(text) > _SNIPPET_CHARS else "")


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Token-boundary phrase containment over whitespace-normalized, casefolded text.

    Padding both sides with a space makes the match align on word boundaries, so "ada" does not
    match inside "adaptive" and a multi-word entity must appear as a contiguous phrase.
    """
    return f" {needle} " in f" {haystack} "


def link_entities(
    question: str,
    kg_repo: KnowledgeGraphRepository,
    tenant_id: str,
    *,
    nodes: Sequence[KgEntity] | None = None,
    aliases: Mapping[tuple[str, str], str] | None = None,
    min_chars: int = _MIN_LINK_CHARS,
) -> list[KgEntity]:
    """Resolve the question's terms to canonical knowledge-graph nodes (deterministic, no LLM).

    Each known node's normalized value (and every folded alias surface form) is matched as a
    token-boundary phrase against the normalized question; longest surface forms win first so a
    multi-word entity beats its own prefix. ``nodes``/``aliases`` may be passed in to avoid re-
    reading them when the caller already loaded them. Returns the linked canonical nodes (deduped).
    """
    node_list = list(nodes) if nodes is not None else kg_repo.list_entities(tenant_id)
    alias_map = dict(aliases) if aliases is not None else kg_repo.alias_map(tenant_id)
    by_id = {n.id: n for n in node_list}

    surfaces: list[tuple[str, str]] = [
        (normalize_ner_name(n.normalized_value), n.id) for n in node_list
    ]
    for (_etype, alias_norm), canonical_id in alias_map.items():
        surfaces.append((normalize_ner_name(alias_norm), canonical_id))
    # Longest surface first: a question naming "m-net telekommunikations gmbh" links the full org
    # node, not the bare "m-net" alias prefix.
    surfaces.sort(key=lambda s: len(s[0]), reverse=True)

    haystack = normalize_ner_name(question)
    seeds: dict[str, KgEntity] = {}
    for surface, canonical_id in surfaces:
        if len(surface) < min_chars or canonical_id not in by_id:
            continue
        if _contains_phrase(haystack, surface):
            seeds.setdefault(canonical_id, by_id[canonical_id])
    return list(seeds.values())


class DefaultGraphRetriever:
    """``GraphRetriever`` over a ``KnowledgeGraphRepository`` (KAG Phase 3, local mode).

    Tenant-scoped, deterministic, additive. Two named entities -> a shortest A<->B path; otherwise a
    bounded neighborhood of the linked seeds. Every returned hit/triple carries the source document/
    chunk of the edge's provenance, so the answer cites real evidence exactly like a hybrid hit.
    """

    def __init__(
        self,
        kg_repo: KnowledgeGraphRepository,
        *,
        documents: DocumentRepository | None = None,
        hops: int = 1,
        max_path_hops: int = 2,
        edge_limit: int = 64,
        min_link_chars: int = _MIN_LINK_CHARS,
    ) -> None:
        self._kg = kg_repo
        self._documents = documents
        self._hops = hops
        self._max_path_hops = max_path_hops
        self._edge_limit = edge_limit
        self._min_link_chars = min_link_chars

    def retrieve(self, tenant_id: str, question: str, *, limit: int = 10) -> GraphRetrieval:
        question = (question or "").strip()
        if not question:
            return GraphRetrieval()
        nodes = self._kg.list_entities(tenant_id)
        if not nodes:
            return GraphRetrieval()
        aliases = self._kg.alias_map(tenant_id)
        seeds = link_entities(
            question,
            self._kg,
            tenant_id,
            nodes=nodes,
            aliases=aliases,
            min_chars=self._min_link_chars,
        )
        if not seeds:
            return GraphRetrieval()
        edges, provenance = self._traverse(tenant_id, seeds)
        if not edges:
            return GraphRetrieval()
        return self._assemble(tenant_id, edges, provenance, {n.id: n for n in nodes}, limit)

    def _traverse(
        self, tenant_id: str, seeds: list[KgEntity]
    ) -> tuple[list[KgEdge], list[KgEdgeProvenance]]:
        # Two distinct entities named -> answer the "how is A connected to B" shape with a path;
        # fall back to the seed neighborhood when there is no path within the hop bound.
        if len(seeds) >= 2:
            edges, prov = self._kg.path_between(
                tenant_id,
                seeds[0].id,
                seeds[1].id,
                max_hops=self._max_path_hops,
                edge_limit=self._edge_limit,
            )
            if edges:
                return edges, prov
        return self._kg.neighborhood(
            tenant_id, [s.id for s in seeds], hops=self._hops, edge_limit=self._edge_limit
        )

    def _assemble(
        self,
        tenant_id: str,
        edges: list[KgEdge],
        provenance: list[KgEdgeProvenance],
        by_id: Mapping[str, KgEntity],
        limit: int,
    ) -> GraphRetrieval:
        prov_by_edge: dict[str, list[KgEdgeProvenance]] = {}
        for p in provenance:
            prov_by_edge.setdefault(p.edge_id, []).append(p)
        # Highest-evidence edges first: a graph-comparable RRF score by rank keeps the strongest
        # relationships near the front of the merged candidate pool.
        ordered = sorted(edges, key=lambda e: (e.evidence_count, e.id), reverse=True)

        triples: list[GraphTriple] = []
        hits: list[SearchHit] = []
        seen_chunks: set[str] = set()
        doc_cache: dict[str, tuple[str | None, str | None]] = {}
        cap_hits = max(limit, 1) * 4

        for rank, edge in enumerate(ordered, start=1):
            subject = by_id.get(edge.src_entity_id)
            obj = by_id.get(edge.dst_entity_id)
            if subject is None or obj is None:
                continue
            evidences = prov_by_edge.get(edge.id, [])
            head = evidences[0] if evidences else None
            triples.append(
                GraphTriple(
                    subject=subject.normalized_value,
                    predicate=edge.predicate,
                    object=obj.normalized_value,
                    document_id=head.document_id if head else "",
                    chunk_id=head.chunk_id if head else None,
                    evidence=head.evidence if head else "",
                )
            )
            for prov in evidences:
                if len(hits) >= cap_hits:
                    break
                key = prov.chunk_id or f"edge:{edge.id}:{prov.id}"
                if key in seen_chunks:
                    continue
                seen_chunks.add(key)
                filename, title = self._doc_display(tenant_id, prov.document_id, doc_cache)
                relation_text = (
                    f"{subject.normalized_value} {edge.predicate} {obj.normalized_value}"
                )
                hits.append(
                    SearchHit(
                        document_id=prov.document_id,
                        chunk_id=prov.chunk_id or f"edge:{edge.id}",
                        original_filename=filename,
                        title=title,
                        snippet=_snippet(prov.evidence or relation_text),
                        text=prov.evidence or relation_text,
                        score=round(1.0 / (_RRF_K + rank), 6),
                    )
                )
        return GraphRetrieval(hits=hits, triples=triples[: max(limit, 1) * 2])

    def _doc_display(
        self, tenant_id: str, document_id: str, cache: dict[str, tuple[str | None, str | None]]
    ) -> tuple[str | None, str | None]:
        if self._documents is None or not document_id:
            return None, None
        if document_id not in cache:
            doc = self._documents.get(tenant_id, document_id)
            cache[document_id] = (
                (doc.original_filename, doc.title) if doc is not None else (None, None)
            )
        return cache[document_id]
