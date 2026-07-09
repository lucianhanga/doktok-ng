"""In-memory entity repository for tests and local/dev runs (tenant-scoped)."""

from __future__ import annotations

from doktok_contracts.schemas import (
    Document,
    DocumentEntity,
    EntitySummary,
    EntityType,
    TokenSuggestion,
)


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

    def delete_for_document_types(
        self,
        tenant_id: str,
        document_id: str,
        entity_types: list[str],
        *,
        source: str | None = None,
        keep_source: str | None = None,
    ) -> None:
        # Source filters keep the two POSTAL_CODE producers' delete scopes disjoint (#528);
        # see the EntityRepository port docstring.
        types = set(entity_types)

        def targeted(e: DocumentEntity) -> bool:
            if e.tenant_id != tenant_id or e.document_id != document_id:
                return False
            if e.entity_type.value not in types:
                return False
            row_source = e.metadata.get("source")
            if source is not None and row_source != source:
                return False
            return not (keep_source is not None and row_source == keep_source)

        self.entities = [e for e in self.entities if not targeted(e)]

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

    def mention_document_ids(
        self,
        tenant_id: str,
        term: str,
        *,
        entity_type: EntityType | None = None,
        cap: int = 10_000,
    ) -> tuple[list[str], int, bool]:
        term_lower = term.lower()
        matched: set[str] = set()
        for e in self.entities:
            if e.tenant_id != tenant_id or not e.normalized_value:
                continue
            if entity_type is not None and e.entity_type != entity_type:
                continue
            if term_lower in e.normalized_value.lower():
                matched.add(e.document_id)
        ids = sorted(matched)
        return ids[:cap], len(ids), len(ids) > cap

    def entity_counts_for_documents(
        self, tenant_id: str, document_ids: list[str]
    ) -> dict[str, int]:
        wanted = set(document_ids)
        counts: dict[str, int] = {}
        for e in self.entities:
            if e.tenant_id != tenant_id or e.document_id not in wanted:
                continue
            counts[e.document_id] = counts.get(e.document_id, 0) + 1
        return counts

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentEntity]:
        return [
            e.model_copy(deep=True)
            for e in self.entities
            if e.tenant_id == tenant_id and e.document_id == document_id
        ]

    def _docs_with_all(self, tenant_id: str, tokens_lower: list[str]) -> set[str]:
        by_doc: dict[str, set[str]] = {}
        for e in self.entities:
            if e.tenant_id != tenant_id or not e.normalized_value:
                continue
            by_doc.setdefault(e.document_id, set()).add(e.normalized_value.lower())
        wanted = set(tokens_lower)
        return {doc for doc, values in by_doc.items() if wanted <= values}

    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]:
        prefix_lower = prefix.lower()
        selected_lower = [s.lower() for s in (selected or [])]
        scope: set[str] | None = None
        if selected_lower:
            scope = self._docs_with_all(tenant_id, selected_lower)
        docs: dict[str, set[str]] = {}
        for e in self.entities:
            if e.tenant_id != tenant_id or not e.normalized_value:
                continue
            if scope is not None and e.document_id not in scope:
                continue
            value = e.normalized_value
            if not value.lower().startswith(prefix_lower) or value.lower() in selected_lower:
                continue
            docs.setdefault(value, set()).add(e.document_id)
        suggestions = [
            TokenSuggestion(value=value, document_count=len(doc_ids))
            for value, doc_ids in docs.items()
        ]
        suggestions.sort(key=lambda s: (-s.document_count, s.value))
        return suggestions[:limit]

    def documents_for_tokens(
        self,
        tenant_id: str,
        tokens: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        # Document resolution requires the document repository; not available in-memory.
        return []
