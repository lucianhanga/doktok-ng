"""In-memory record repository for tests/dev (tenant-scoped). M6.3."""

from __future__ import annotations

from doktok_contracts.schemas import ExtractedRecord


class InMemoryRecordRepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[ExtractedRecord]] = {}

    def replace_for_document(
        self, tenant_id: str, document_id: str, records: list[ExtractedRecord]
    ) -> None:
        self._records[(tenant_id, document_id)] = [r.model_copy(deep=True) for r in records]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[ExtractedRecord]:
        return [r.model_copy(deep=True) for r in self._records.get((tenant_id, document_id), [])]
