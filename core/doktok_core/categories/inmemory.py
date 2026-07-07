"""In-memory category repository for tests/dev (tenant-scoped). Caps mirror the DB triggers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import Category, CategorySummary, Document

from doktok_core.enrichment import MAX_CATEGORIES_PER_DOCUMENT, MAX_CATEGORIES_PER_TENANT


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _similarity(a: str, b: str) -> float:
    """Trigram Jaccard similarity, mirroring Postgres pg_trgm closely enough for tests."""
    ta, tb = _trigrams(a), _trigrams(b)
    union = ta | tb
    return len(ta & tb) / len(union) if union else 0.0


class InMemoryCategoryRepository:
    def __init__(self) -> None:
        self._cats: dict[str, Category] = {}
        self._links: dict[tuple[str, str], list[str]] = {}

    def list_active(self, tenant_id: str) -> list[Category]:
        return sorted(
            (c for c in self._cats.values() if c.tenant_id == tenant_id and c.status == "active"),
            key=lambda c: c.name,
        )

    def find_by_normalized(self, tenant_id: str, normalized: str) -> Category | None:
        return next(
            (c for c in self.list_active(tenant_id) if c.normalized == normalized),
            None,
        )

    def _best(self, tenant_id: str, normalized: str) -> tuple[Category | None, float]:
        best: Category | None = None
        best_score = 0.0
        for c in self.list_active(tenant_id):
            score = _similarity(c.normalized, normalized)
            if score > best_score:
                best, best_score = c, score
        return best, best_score

    def find_similar(
        self, tenant_id: str, normalized: str, *, threshold: float = 0.55
    ) -> Category | None:
        best, score = self._best(tenant_id, normalized)
        return best if score >= threshold else None

    def find_nearest(self, tenant_id: str, normalized: str) -> Category | None:
        return self._best(tenant_id, normalized)[0]

    def active_count(self, tenant_id: str) -> int:
        return len(self.list_active(tenant_id))

    def create(self, tenant_id: str, name: str, normalized: str) -> Category | None:
        if self.active_count(tenant_id) >= MAX_CATEGORIES_PER_TENANT:
            return None
        existing = self.find_by_normalized(tenant_id, normalized)
        if existing is not None:
            return existing
        category = Category(
            id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            name=name,
            normalized=normalized,
            status="active",
            created_at=datetime.now(UTC),
        )
        self._cats[category.id] = category
        return category

    def set_document_categories(
        self, tenant_id: str, document_id: str, category_ids: list[str]
    ) -> None:
        self._links[(tenant_id, document_id)] = list(category_ids)[:MAX_CATEGORIES_PER_DOCUMENT]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[Category]:
        ids = self._links.get((tenant_id, document_id), [])
        return [self._cats[i] for i in ids if i in self._cats]

    def list_summary(self, tenant_id: str) -> list[CategorySummary]:
        counts: dict[str, int] = {}
        for (tid, _doc), ids in self._links.items():
            if tid != tenant_id:
                continue
            for cid in ids:
                cat = self._cats.get(cid)
                if cat is not None:
                    counts[cat.name] = counts.get(cat.name, 0) + 1
        summaries = [
            CategorySummary(name=c.name, document_count=counts.get(c.name, 0))
            for c in self.list_active(tenant_id)
        ]
        return sorted(summaries, key=lambda s: (-s.document_count, s.name))

    def documents_for_category(
        self, tenant_id: str, name: str, *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        # The in-memory repo holds no documents; the document<->category join is covered by the
        # Postgres integration test.
        return []

    def primary_categories(self, tenant_id: str, document_ids: list[str]) -> dict[str, str]:
        # Pick each document's rank-0 category: the first entry in the stored list, which mirrors
        # the classifier's intended primary.  Mirrors the Postgres ORDER BY l.rank ASC / rn=1
        # window query; name tiebreak is not needed here because the list position is authoritative.
        wanted = set(document_ids)
        result: dict[str, str] = {}
        for (tid, doc), ids in self._links.items():
            if tid != tenant_id or doc not in wanted:
                continue
            names = [self._cats[i].name for i in ids if i in self._cats]
            if names:
                result[doc] = names[0]
        return result
