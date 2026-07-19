"""In-memory audit log for tests and local/dev runs (tenant-scoped, append-only)."""

from __future__ import annotations

from doktok_contracts.schemas import AuditEvent


class InMemoryAuditLogRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._ids: set[str] = set()

    def record(self, event: AuditEvent) -> None:
        # Insert-if-absent by id (mirrors the Postgres ON CONFLICT DO NOTHING), so a deterministic
        # id can dedup repeated near-identical events such as a double-fired document view.
        if event.id in self._ids:
            return
        self._ids.add(event.id)
        self._events.append(event.model_copy(deep=True))

    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        event_type_prefixes: tuple[str, ...] | None = None,
    ) -> list[AuditEvent]:
        matches = [
            e
            for e in reversed(self._events)
            if e.tenant_id == tenant_id
            and (document_id is None or e.document_id == document_id)
            and (
                event_type_prefixes is None
                or any(e.event_type.startswith(prefix) for prefix in event_type_prefixes)
            )
        ]
        return [e.model_copy(deep=True) for e in matches[offset : offset + limit]]
