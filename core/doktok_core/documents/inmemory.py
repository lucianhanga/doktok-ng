"""In-memory document repository for tests and local/dev runs (tenant-scoped, ADR-0007)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import cmp_to_key
from typing import Any

from doktok_contracts.errors import DuplicateActiveDocumentError
from doktok_contracts.schemas import (
    Document,
    DocumentSort,
    DocumentStatus,
    EntityType,
    ListAnchor,
    SortDir,
    TokenMatch,
)

# A sort value can be a datetime (acquired), date (created), int (entities/chunks), string, or None.
type _SortVal = datetime | date | int | str | None


def _cmp_scalar(x: Any, y: Any) -> int:
    """Three-way compare two same-typed, non-null sort values (always same type per sort key)."""
    return int((x > y) - (x < y))


class InMemoryDocumentRepository:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        # Test seams for the join-based filters the real repo derives in SQL: ids of documents with
        # a non-done feature, the active category names per document, and the (entity_type, value)
        # tokens per document (for token filtering).
        self.attention_ids: set[str] = set()
        self.categories_by_doc: dict[str, set[str]] = {}
        self.tokens_by_doc: dict[str, set[tuple[str, str]]] = {}
        # Seams for the new count sort keys (entity/chunk counts per document id).
        self.entity_counts_by_doc: dict[str, int] = {}
        self.chunk_counts_by_doc: dict[str, int] = {}

    def add(self, document: Document) -> None:
        if document.id in self._docs:
            raise ValueError(f"document {document.id} already exists")
        if document.status is DocumentStatus.ACTIVE and self.find_active_by_sha256(
            document.tenant_id, document.sha256
        ):
            raise DuplicateActiveDocumentError(
                f"active document with sha {document.sha256[:8]} already exists"
            )
        self._docs[document.id] = document.model_copy(deep=True)

    def find_active_by_sha256(self, tenant_id: str, sha256: str) -> str | None:
        for d in self._docs.values():
            if (
                d.tenant_id == tenant_id
                and d.sha256 == sha256
                and d.status is DocumentStatus.ACTIVE
            ):
                return d.id
        return None

    def get(self, tenant_id: str, document_id: str) -> Document | None:
        doc = self._docs.get(document_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return doc.model_copy(deep=True)

    def get_many(self, tenant_id: str, document_ids: list[str]) -> list[Document]:
        result: list[Document] = []
        for i in document_ids:
            doc = self._docs.get(i)
            if doc is not None and doc.tenant_id == tenant_id:
                result.append(doc.model_copy(deep=True))
        return result

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

    def set_unidentifiable(self, tenant_id: str, document_id: str, *, value: bool | None) -> None:
        doc = self._docs.get(document_id)
        if doc is not None and doc.tenant_id == tenant_id:
            doc.unidentifiable = value

    def activate(
        self,
        tenant_id: str,
        document_id: str,
        *,
        storage_path: str,
        metadata: dict[str, object],
    ) -> bool:
        doc = self._docs.get(document_id)
        if doc is None or doc.tenant_id != tenant_id or doc.status is not DocumentStatus.PROCESSING:
            return False
        if self.find_active_by_sha256(tenant_id, doc.sha256):
            raise DuplicateActiveDocumentError(
                f"active document with sha {doc.sha256[:8]} already exists"
            )
        now = datetime.now(UTC)
        doc.status = DocumentStatus.ACTIVE
        doc.storage_path = storage_path
        doc.metadata = {**doc.metadata, **metadata}
        doc.activated_at = now
        doc.ingested_at = now
        return True

    def _sort_value(self, d: Document, sort: DocumentSort) -> _SortVal:
        if sort is DocumentSort.ACQUIRED:
            return d.created_at
        if sort is DocumentSort.CREATED:
            return d.document_date
        if sort is DocumentSort.TITLE:
            return d.title.lower() if d.title else None
        if sort is DocumentSort.STATUS:
            return d.status.value
        if sort is DocumentSort.ENTITIES:
            return self.entity_counts_by_doc.get(d.id, 0)
        if sort is DocumentSort.CHUNKS:
            return self.chunk_counts_by_doc.get(d.id, 0)
        cats = self.categories_by_doc.get(d.id, set())
        return min(cats) if cats else None  # CATEGORY: alphabetically-first active category

    def _matches_tokens(
        self,
        d: Document,
        tokens: tuple[str, ...],
        token_type: EntityType | None,
        token_match: TokenMatch,
    ) -> bool:
        if not tokens:
            return True
        pairs = self.tokens_by_doc.get(d.id, set())
        values = {v for (t, v) in pairs if token_type is None or t == token_type.value}
        requested = set(tokens)
        if token_match is TokenMatch.ALL:
            return requested <= values
        return bool(requested & values)

    def _filtered(
        self,
        tenant_id: str,
        *,
        status: DocumentStatus | None,
        category: str | None,
        needs_attention: bool,
        unidentifiable: bool | None,
        title: str | None,
        tokens: tuple[str, ...],
        token_type: EntityType | None,
        token_match: TokenMatch,
    ) -> list[Document]:
        title_clean = title.strip().lower() if title and title.strip() else None
        return [
            d
            for d in self._docs.values()
            if d.tenant_id == tenant_id
            and (status is None or d.status == status)
            and (not needs_attention or d.id in self.attention_ids)
            and (category is None or category in self.categories_by_doc.get(d.id, set()))
            and (title_clean is None or (d.title is not None and title_clean in d.title.lower()))
            # True = only flagged; False = exclude flagged (NULL 'unassessed' stays shown) - matches
            # the Postgres `IS NOT TRUE` semantics.
            and (
                unidentifiable is None
                or (d.unidentifiable is True if unidentifiable else d.unidentifiable is not True)
            )
            and self._matches_tokens(d, tokens, token_type, token_match)
        ]

    def list_documents(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        cursor: ListAnchor | None = None,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
        unidentifiable: bool | None = None,
        sort: DocumentSort = DocumentSort.ACQUIRED,
        direction: SortDir = SortDir.DESC,
        title: str | None = None,
        tokens: tuple[str, ...] = (),
        token_type: EntityType | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
    ) -> tuple[list[Document], int, ListAnchor | None]:
        docs = self._filtered(
            tenant_id,
            status=status,
            category=category,
            needs_attention=needs_attention,
            unidentifiable=unidentifiable,
            title=title,
            tokens=tokens,
            token_type=token_type,
            token_match=token_match,
        )

        def cmp(a: tuple[_SortVal, str], b: tuple[_SortVal, str]) -> int:
            av, ai = a
            bv, bi = b
            if av is None and bv is None:
                base = _cmp_scalar(ai, bi)
            elif av is None:
                return 1  # nulls always sort last, regardless of direction
            elif bv is None:
                return -1
            else:
                base = _cmp_scalar(av, bv) or _cmp_scalar(ai, bi)
            return -base if direction is SortDir.DESC else base

        docs.sort(
            key=cmp_to_key(
                lambda x, y: cmp(
                    (self._sort_value(x, sort), x.id), (self._sort_value(y, sort), y.id)
                )
            )
        )
        total = len(docs)
        if cursor is not None:
            anchor = (cursor.value, cursor.doc_id)
            docs = [d for d in docs if cmp((self._sort_value(d, sort), d.id), anchor) > 0]
        page = docs[:limit]
        next_anchor = (
            ListAnchor(
                sort=sort,
                direction=direction,
                value=self._sort_value(page[-1], sort),
                doc_id=page[-1].id,
            )
            if len(docs) > limit and page
            else None
        )
        return [d.model_copy(deep=True) for d in page], total, next_anchor

    def list_document_ids(
        self,
        tenant_id: str,
        *,
        status: DocumentStatus | None = None,
        category: str | None = None,
        needs_attention: bool = False,
        unidentifiable: bool | None = None,
        title: str | None = None,
        tokens: tuple[str, ...] = (),
        token_type: EntityType | None = None,
        token_match: TokenMatch = TokenMatch.ALL,
        cap: int = 10_000,
    ) -> tuple[list[str], int, bool]:
        ids = sorted(
            d.id
            for d in self._filtered(
                tenant_id,
                status=status,
                category=category,
                needs_attention=needs_attention,
                unidentifiable=unidentifiable,
                title=title,
                tokens=tokens,
                token_type=token_type,
                token_match=token_match,
            )
        )
        return ids[:cap], len(ids), len(ids) > cap

    def delete(self, tenant_id: str, document_id: str) -> None:
        doc = self._docs.get(document_id)
        if doc is not None and doc.tenant_id == tenant_id:
            del self._docs[document_id]
