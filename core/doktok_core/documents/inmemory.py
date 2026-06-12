"""In-memory document repository for tests and local/dev runs (tenant-scoped, ADR-0007)."""

from __future__ import annotations

from datetime import date, datetime

from doktok_contracts.schemas import Document, DocumentStatus


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        # Test seams for the join-based filters the real repo derives in SQL: ids of documents with
        # a non-done feature, and the active category names per document.
        self.attention_ids: set[str] = set()
        self.categories_by_doc: dict[str, set[str]] = {}

    def add(self, document: Document) -> None:
        if document.id in self._docs:
            raise ValueError(f"document {document.id} already exists")
        self._docs[document.id] = document.model_copy(deep=True)

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        doc = self._docs.get(document_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return doc.model_copy(deep=True)

    def set_metadata(
        self,
        tenant_id: str,
        document_id: str,
        *,
        title: str | None,
        document_date: date | None,
        location: str | None,
        summary: str | None,
    ) -> None:
        doc = self._docs.get(document_id)
        if doc is None or doc.tenant_id != tenant_id:
            return
        if title is not None:
            doc.title = title
        doc.document_date = document_date
        doc.location = location
        doc.summary = summary

    def list_documents(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: tuple[datetime, str] | None = None,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
    ) -> tuple[list[Document], int, tuple[datetime, str] | None]:
        docs = [
            d
            for d in self._docs.values()
            if d.tenant_id == tenant_id
            and (status is None or d.status == status)
            and (not needs_attention or d.id in self.attention_ids)
            and (category is None or category in self.categories_by_doc.get(d.id, set()))
        ]
        # Keyset order: (created_at DESC, id DESC), a total order matching the SQL.
        docs.sort(key=lambda d: (d.created_at, d.id), reverse=True)
        total = len(docs)
        if cursor is not None:
            docs = [d for d in docs if (d.created_at, d.id) < cursor]
        page = docs[:limit]
        next_anchor = (page[-1].created_at, page[-1].id) if len(docs) > limit and page else None
        return [d.model_copy(deep=True) for d in page], total, next_anchor

    def delete(self, tenant_id: str, document_id: str) -> None:
        doc = self._docs.get(document_id)
        if doc is not None and doc.tenant_id == tenant_id:
            del self._docs[document_id]
