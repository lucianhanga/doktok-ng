"""In-memory knowledge-graph repository for tests and local/dev runs (tenant-scoped, KAG Phase 1+2).

Mirrors the Postgres adapter's semantics exactly: ``upsert_entities`` keeps the first-seen node for
an id (deterministic identity, so re-running never changes it), and the replace-by-document calls
delete-then-insert a document's mention/provenance rows.
"""

from __future__ import annotations

from doktok_contracts.schemas import KgEdge, KgEdgeProvenance, KgEntity, KgEntityMention


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
        # Step 4: recompute evidence_count for all affected edges
        all_edge_ids = {e.id for e in edges} | {p.edge_id for p in provenance}
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
