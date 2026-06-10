"""In-memory entity repository for tests and local/dev runs (tenant-scoped)."""

from __future__ import annotations

from doktok_contracts.schemas import Document, DocumentEntity, EntitySummary, EntityType


class InMemoryEntityRepository:
    def __init__(self) -> None:
        self.entities: list[DocumentEntity] = []

    def add_entities(self, entities: list[DocumentEntity]) -> None:
        self.entities.extend(e.model_copy(deep=True) for e in entities)

    def delete_for_document(self, tenant_id: str, document_id: str) -> None:
        self.entities = [
            e
            for e in self.entities
            if not (e.tenant_id == tenant_id and e.document_id == document_id)
        ]

    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntitySummary]:
        docs: dict[tuple[EntityType, str], set[str]] = {}
        occurrences: dict[tuple[EntityType, str], int] = {}
        for e in self.entities:
            if e.tenant_id != tenant_id:
                continue
            if entity_type is not None and e.entity_type != entity_type:
                continue
            key = (e.entity_type, e.normalized_value or "")
            docs.setdefault(key, set()).add(e.document_id)
            occurrences[key] = occurrences.get(key, 0) + e.frequency
        summaries = [
            EntitySummary(
                entity_type=key[0],
                normalized_value=key[1],
                document_count=len(docs[key]),
                occurrences=occurrences[key],
            )
            for key in docs
        ]
        summaries.sort(key=lambda s: (-s.occurrences, s.normalized_value))
        return summaries[offset : offset + limit]

    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        # Document resolution requires the document repository; not available in-memory.
        return []
