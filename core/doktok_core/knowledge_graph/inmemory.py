"""In-memory knowledge-graph repository for tests and local/dev runs (tenant-scoped, KAG Phase 1).

Mirrors the Postgres adapter's semantics exactly: ``upsert_entities`` keeps the first-seen node for
an id (deterministic identity, so re-running never changes it), and the replace-by-document call
deletes-then-inserts a document's mention rows.
"""

from __future__ import annotations

from doktok_contracts.schemas import KgEntity, KgEntityMention


class InMemoryKnowledgeGraphRepository:
    def __init__(self) -> None:
        # node id -> node
        self._entities: dict[str, KgEntity] = {}
        # mention_id -> mention (PK is mention_id, one canonical per mention)
        self._mentions: dict[str, KgEntityMention] = {}

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
