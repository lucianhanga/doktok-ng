"""In-memory document repository for tests and local/dev runs (tenant-scoped, ADR-0007)."""

from __future__ import annotations

from datetime import date

from doktok_contracts.schemas import Document, DocumentStatus


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}

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
        limit: int = 50,
        offset: int = 0,
        *,
        status: DocumentStatus | None = None,
    ) -> list[Document]:
        docs = [
            d
            for d in reversed(self._docs.values())
            if d.tenant_id == tenant_id and (status is None or d.status == status)
        ]
        return [d.model_copy(deep=True) for d in docs[offset : offset + limit]]

    def delete(self, tenant_id: str, document_id: str) -> None:
        doc = self._docs.get(document_id)
        if doc is not None and doc.tenant_id == tenant_id:
            del self._docs[document_id]
