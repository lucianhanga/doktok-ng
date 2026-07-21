"""In-memory tag repository (epic #543; mirrors the Postgres contract)."""

from __future__ import annotations

from doktok_contracts.schemas import Tag


def _token_jaccard(a: str, b: str) -> float:
    """Token-set Jaccard similarity over normalized keys: 'rome trip' == 'trip rome' (1.0)."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class InMemoryTagRepository:
    def __init__(self) -> None:
        self._tags: dict[str, Tag] = {}
        self._links: set[tuple[str, str, str]] = set()  # (tenant_id, document_id, tag_id)

    def create_tag(self, tag: Tag) -> None:
        if any(
            t.tenant_id == tag.tenant_id and t.normalized == tag.normalized and t.status == "active"
            for t in self._tags.values()
        ):
            raise ValueError(f"tag with normalized key {tag.normalized!r} already exists")
        self._tags[tag.id] = tag.model_copy(deep=True)

    def get_tag(self, tenant_id: str, tag_id: str) -> Tag | None:
        tag = self._tags.get(tag_id)
        if tag is None or tag.tenant_id != tenant_id:
            return None
        return tag.model_copy(deep=True)

    def find_by_normalized(self, tenant_id: str, normalized: str) -> Tag | None:
        for t in self._tags.values():
            if t.tenant_id == tenant_id and t.normalized == normalized and t.status == "active":
                return t.model_copy(deep=True)
        return None

    def list_tags(self, tenant_id: str, *, status: str = "active") -> list[Tag]:
        rows = [
            t.model_copy(deep=True)
            for t in self._tags.values()
            if t.tenant_id == tenant_id and t.status == status
        ]
        rows.sort(key=lambda t: t.name)
        return rows

    def update_tag(
        self,
        tenant_id: str,
        tag_id: str,
        *,
        name: str | None = None,
        normalized: str | None = None,
        description: str | None = None,
        color: str | None = None,
    ) -> Tag | None:
        tag = self._tags.get(tag_id)
        if tag is None or tag.tenant_id != tenant_id:
            return None
        if name is not None:
            tag.name = name
        if normalized is not None:
            tag.normalized = normalized
        if description is not None:
            tag.description = description
        if color is not None:
            tag.color = color
        return tag.model_copy(deep=True)

    def set_tag_status(
        self, tenant_id: str, tag_id: str, status: str, *, merged_into: str | None = None
    ) -> None:
        tag = self._tags.get(tag_id)
        if tag is not None and tag.tenant_id == tenant_id:
            tag.status = status
            tag.merged_into = merged_into

    def delete_tag(self, tenant_id: str, tag_id: str) -> None:
        tag = self._tags.get(tag_id)
        if tag is not None and tag.tenant_id == tenant_id:
            del self._tags[tag_id]
            self._links = {
                link for link in self._links if not (link[0] == tenant_id and link[2] == tag_id)
            }

    def find_similar(self, tenant_id: str, normalized: str, *, limit: int = 3) -> list[Tag]:
        scored = sorted(
            ((t, _token_jaccard(normalized, t.normalized)) for t in self.list_tags(tenant_id)),
            key=lambda kv: kv[1],
            reverse=True,
        )
        return [t for t, score in scored[:limit] if score >= 0.6]

    def link(self, tenant_id: str, document_id: str, tag_id: str) -> None:
        self._links.add((tenant_id, document_id, tag_id))

    def unlink(self, tenant_id: str, document_id: str, tag_id: str) -> None:
        self._links.discard((tenant_id, document_id, tag_id))

    def list_for_document(self, tenant_id: str, document_id: str) -> list[Tag]:
        ids = {link[2] for link in self._links if link[0] == tenant_id and link[1] == document_id}
        rows = [self._tags[i].model_copy(deep=True) for i in ids if i in self._tags]
        rows.sort(key=lambda t: t.name)
        return rows

    def count_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tid, doc_id, _tag_id in self._links:
            if tid == tenant_id and doc_id in document_ids:
                counts[doc_id] = counts.get(doc_id, 0) + 1
        return counts

    def tags_for_documents(self, tenant_id: str, document_ids: list[str]) -> dict[str, list[Tag]]:
        wanted = set(document_ids)
        result: dict[str, list[Tag]] = {}
        for tid, doc_id, tag_id in self._links:
            if tid == tenant_id and doc_id in wanted and tag_id in self._tags:
                result.setdefault(doc_id, []).append(self._tags[tag_id].model_copy(deep=True))
        for doc_tags in result.values():
            doc_tags.sort(key=lambda t: t.name)
        return result

    def document_count(self, tenant_id: str, tag_id: str) -> int:
        return sum(1 for link in self._links if link[0] == tenant_id and link[2] == tag_id)

    def tag_counts(self, tenant_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for tid, _doc_id, tag_id in self._links:
            if tid == tenant_id:
                counts[tag_id] = counts.get(tag_id, 0) + 1
        return counts
