"""In-memory audit log for tests and local/dev runs (tenant-scoped, append-only)."""

from __future__ import annotations

from doktok_contracts.schemas import AuditEvent


class InMemoryAuditLogRepository:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event.model_copy(deep=True))

    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        matches = [
            e
            for e in reversed(self._events)
            if e.tenant_id == tenant_id and (document_id is None or e.document_id == document_id)
        ]
        return [e.model_copy(deep=True) for e in matches[offset : offset + limit]]
