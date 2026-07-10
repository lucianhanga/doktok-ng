"""In-memory ``UserPreferenceRepository`` for tests and DB-less local runs (#558)."""

from __future__ import annotations

from typing import Any


class InMemoryUserPreferenceRepository:
    def __init__(self) -> None:
        # (tenant_id, subject) -> {key: value}
        self._store: dict[tuple[str, str], dict[str, Any]] = {}

    def get_all(self, tenant_id: str, subject: str) -> dict[str, Any]:
        return dict(self._store.get((tenant_id, subject), {}))

    def set_many(self, tenant_id: str, subject: str, values: dict[str, Any]) -> None:
        self._store.setdefault((tenant_id, subject), {}).update(values)

    def delete(self, tenant_id: str, subject: str, key: str) -> None:
        self._store.get((tenant_id, subject), {}).pop(key, None)
