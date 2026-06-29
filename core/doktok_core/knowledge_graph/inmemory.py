"""In-memory knowledge-graph repository for tests and local/dev runs (tenant-scoped, KAG Phase 1+2).

Mirrors the Postgres adapter's semantics exactly: ``upsert_entities`` keeps the first-seen node for
an id (deterministic identity, so re-running never changes it), and the replace-by-document calls
delete-then-insert a document's mention/provenance rows.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from doktok_contracts.schemas import (
    AliasFold,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgEntityMention,
)

from doktok_core.knowledge_graph.predicates import canonical_edge_id


class InMemoryKnowledgeGraphRepository:
    def __init__(self) -> None:
        # node id -> node
        self._entities: dict[str, KgEntity] = {}
        # mention_id -> mention (PK is mention_id, one canonical per mention)
        self._mentions: dict[str, KgEntityMention] = {}
        # edge id -> edge
        self._edges: dict[str, KgEdge] = {}
        # provenance id -> provenance
        self._provenance: dict[str, KgEdgeProvenance] = {}
        # (tenant_id, entity_type, alias_normalized) -> canonical_entity_id
        self._aliases: dict[tuple[str, str, str], str] = {}

    def upsert_entities(self, entities: list[KgEntity]) -> None:
        for entity in entities:
            # DO NOTHING on conflict: the node identity is immutable under deterministic resolution.
            self._entities.setdefault(entity.id, entity.model_copy(deep=True))

    def replace_mentions_for_document(
        self, tenant_id: str, document_id: str, mentions: list[KgEntityMention]
    ) -> None:
        self._mentions = {
            mid: m
            for mid, m in self._mentions.items()
            if not (m.tenant_id == tenant_id and m.document_id == document_id)
        }
        for m in mentions:
            self._mentions[m.mention_id] = m.model_copy(deep=True)

    def get_entity(self, tenant_id: str, entity_id: str) -> KgEntity | None:
        entity = self._entities.get(entity_id)
        if entity is None or entity.tenant_id != tenant_id:
            return None
        return entity.model_copy(deep=True)

    def mentions_for_document(self, tenant_id: str, document_id: str) -> list[KgEntityMention]:
        return [
            m.model_copy(deep=True)
            for m in self._mentions.values()
            if m.tenant_id == tenant_id and m.document_id == document_id
        ]

    def mentions_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEntityMention]:
        return [
            m.model_copy(deep=True)
            for m in self._mentions.values()
            if m.tenant_id == tenant_id and m.canonical_entity_id == entity_id
        ]

    def entity_count(self, tenant_id: str) -> int:
        return sum(1 for e in self._entities.values() if e.tenant_id == tenant_id)

    # ------------------------------------------------------------------ Phase 2: edges

    def replace_edges_for_document(
        self,
        tenant_id: str,
        document_id: str,
        edges: list[KgEdge],
        provenance: list[KgEdgeProvenance],
    ) -> None:
        # Edges that currently draw provenance from this document - they may lose evidence below and
        # must be recomputed + pruned even if absent from the new edges/provenance (parity with the
        # Postgres repo; otherwise an orphaned edge keeps a stale evidence_count).
        prior_edge_ids = {
            p.edge_id
            for p in self._provenance.values()
            if p.tenant_id == tenant_id and p.document_id == document_id
        }
        # Step 1: remove old provenance for this document
        self._provenance = {
            pid: p
            for pid, p in self._provenance.items()
            if not (p.tenant_id == tenant_id and p.document_id == document_id)
        }
        # Step 2: upsert edge rows (DO NOTHING on conflict - keep first-seen identity fields)
        for edge in edges:
            if edge.id not in self._edges:
                self._edges[edge.id] = edge.model_copy(deep=True)
        # Step 3: insert new provenance rows
        for prov in provenance:
            self._provenance[prov.id] = prov.model_copy(deep=True)
        # Step 4: recompute evidence_count for all affected edges (new + those that lost provenance)
        all_edge_ids = prior_edge_ids | {e.id for e in edges} | {p.edge_id for p in provenance}
        for eid in all_edge_ids:
            count = sum(1 for p in self._provenance.values() if p.edge_id == eid)
            if eid in self._edges:
                stored = self._edges[eid]
                self._edges[eid] = stored.model_copy(update={"evidence_count": count})
        # Step 5: prune edges with zero evidence_count
        self._edges = {eid: e for eid, e in self._edges.items() if e.evidence_count > 0}

    def edges_for_entity(self, tenant_id: str, entity_id: str) -> list[KgEdge]:
        return [
            e.model_copy(deep=True)
            for e in self._edges.values()
            if e.tenant_id == tenant_id
            and (e.src_entity_id == entity_id or e.dst_entity_id == entity_id)
        ]

    def edge_count(self, tenant_id: str) -> int:
        return sum(1 for e in self._edges.values() if e.tenant_id == tenant_id)

    # ------------------------------------------------------------------ Phase 3: traversal

    def neighborhood(
        self,
        tenant_id: str,
        entity_ids: Sequence[str],
        *,
        hops: int = 1,
        edge_limit: int = 64,
    ) -> tuple[list[KgEdge], list[KgEdgeProvenance]]:
        tenant_edges = [e for e in self._edges.values() if e.tenant_id == tenant_id]
        reached: set[str] = set(entity_ids)
        frontier: set[str] = set(entity_ids)
        for _ in range(max(0, hops)):
            nxt: set[str] = set()
            for e in tenant_edges:
                if e.src_entity_id in frontier and e.dst_entity_id not in reached:
                    nxt.add(e.dst_entity_id)
                if e.dst_entity_id in frontier and e.src_entity_id not in reached:
                    nxt.add(e.src_entity_id)
            if not nxt:
                break
            reached |= nxt
            frontier = nxt
        edges = [
            e for e in tenant_edges if e.src_entity_id in reached and e.dst_entity_id in reached
        ]
        edges.sort(key=lambda e: (e.evidence_count, e.id), reverse=True)
        edges = edges[:edge_limit]
        prov = self._provenance_for(tenant_id, {e.id for e in edges})
        return [e.model_copy(deep=True) for e in edges], prov

    def path_between(
        self,
        tenant_id: str,
        src_entity_id: str,
        dst_entity_id: str,
        *,
        max_hops: int = 2,
        edge_limit: int = 64,
    ) -> tuple[list[KgEdge], list[KgEdgeProvenance]]:
        if src_entity_id == dst_entity_id:
            return [], []
        adjacency: dict[str, list[tuple[str, KgEdge]]] = {}
        for e in self._edges.values():
            if e.tenant_id != tenant_id:
                continue
            adjacency.setdefault(e.src_entity_id, []).append((e.dst_entity_id, e))
            adjacency.setdefault(e.dst_entity_id, []).append((e.src_entity_id, e))
        queue: deque[tuple[str, list[KgEdge]]] = deque([(src_entity_id, [])])
        visited: set[str] = {src_entity_id}
        while queue:
            node, path = queue.popleft()
            if len(path) >= max(1, max_hops):
                continue
            for neighbor, edge in adjacency.get(node, []):
                if neighbor == dst_entity_id:
                    edges = (path + [edge])[:edge_limit]
                    prov = self._provenance_for(tenant_id, {e.id for e in edges})
                    return [e.model_copy(deep=True) for e in edges], prov
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [edge]))
        return [], []

    def _provenance_for(self, tenant_id: str, edge_ids: set[str]) -> list[KgEdgeProvenance]:
        return [
            p.model_copy(deep=True)
            for p in self._provenance.values()
            if p.tenant_id == tenant_id and p.edge_id in edge_ids
        ]

    # ------------------------------------------------------------------ alias-folding tier

    def list_entities(self, tenant_id: str) -> list[KgEntity]:
        return [
            e.model_copy(deep=True) for e in self._entities.values() if e.tenant_id == tenant_id
        ]

    def alias_map(self, tenant_id: str) -> dict[tuple[str, str], str]:
        return {
            (etype, alias): canonical
            for (tid, etype, alias), canonical in self._aliases.items()
            if tid == tenant_id
        }

    def resolve_aliases(self, tenant_id: str, folds: list[AliasFold]) -> int:
        merged = 0
        for fold in folds:
            # Record the mapping (so the merge survives re-ingestion) even if the node is already
            # gone - keeps the pass idempotent and the alias map complete.
            self._aliases[(tenant_id, fold.alias_type, fold.alias_normalized)] = fold.canonical_id
            # Re-point alias rows that targeted the folded node (chained merge across passes).
            for key, canonical in list(self._aliases.items()):
                if key[0] == tenant_id and canonical == fold.alias_id:
                    self._aliases[key] = fold.canonical_id
            if fold.alias_id not in self._entities:
                continue  # already folded in a prior run -> no-op
            self._repoint_mentions(tenant_id, fold.alias_id, fold.canonical_id)
            self._repoint_edges(tenant_id, fold.alias_id, fold.canonical_id)
            del self._entities[fold.alias_id]
            merged += 1
        return merged

    def _repoint_mentions(self, tenant_id: str, alias_id: str, canonical_id: str) -> None:
        for mid, m in list(self._mentions.items()):
            if m.tenant_id == tenant_id and m.canonical_entity_id == alias_id:
                self._mentions[mid] = m.model_copy(update={"canonical_entity_id": canonical_id})

    def _repoint_edges(self, tenant_id: str, alias_id: str, canonical_id: str) -> None:
        affected = [
            e
            for e in self._edges.values()
            if e.tenant_id == tenant_id
            and (e.src_entity_id == alias_id or e.dst_entity_id == alias_id)
        ]
        touched: set[str] = set()
        for edge in affected:
            new_src = canonical_id if edge.src_entity_id == alias_id else edge.src_entity_id
            new_dst = canonical_id if edge.dst_entity_id == alias_id else edge.dst_entity_id
            new_id = canonical_edge_id(tenant_id, new_src, edge.predicate, new_dst)
            if new_id == edge.id:
                continue
            # Ensure the surviving target edge exists (DO NOTHING if it already did - merge case).
            if new_id not in self._edges:
                self._edges[new_id] = edge.model_copy(
                    update={
                        "id": new_id,
                        "src_entity_id": new_src,
                        "dst_entity_id": new_dst,
                        "evidence_count": 0,
                    }
                )
            # Move the old edge's provenance onto the survivor, then drop the old edge.
            for pid, p in list(self._provenance.items()):
                if p.edge_id == edge.id:
                    self._provenance[pid] = p.model_copy(update={"edge_id": new_id})
            del self._edges[edge.id]
            touched.add(new_id)
        # Recompute evidence_count for survivors and prune any that ended up with none.
        for eid in touched:
            count = sum(1 for p in self._provenance.values() if p.edge_id == eid)
            if eid in self._edges:
                if count == 0:
                    del self._edges[eid]
                else:
                    self._edges[eid] = self._edges[eid].model_copy(update={"evidence_count": count})
