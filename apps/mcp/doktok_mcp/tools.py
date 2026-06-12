"""Read-only MCP tool logic (M8).

Pure functions mapping each MCP tool to a tenant-scoped repository read. They are read-only by
construction (they only call read methods) and always scope by the resolved tenant - the tenant is
never a tool argument, so a caller cannot read another tenant's data. Kept free of the MCP transport
so they are unit-testable without a client/server.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from doktok_contracts.ports import DocumentRepository, RecordRepository, Retriever
from doktok_contracts.schemas import AggregationIntent, DocumentStatus

# Allowlist of exposed tool names (the server registers exactly these).
TOOL_NAMES = ("search_documents", "list_documents", "aggregate_records")


def search_documents(
    retriever: Retriever, tenant_id: str, query: str, limit: int = 10
) -> list[dict[str, Any]]:
    """Hybrid (semantic + full-text) search over the tenant's documents."""
    limit = max(1, min(limit, 50))
    hits = retriever.search(tenant_id, query, limit)
    return [
        {
            "document_id": h.document_id,
            "title": h.title,
            "filename": h.original_filename,
            "page_start": h.page_start,
            "snippet": h.snippet,
            "score": h.score,
        }
        for h in hits
    ]


def list_documents(
    repo: DocumentRepository, tenant_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """List the tenant's active documents with their enrichment metadata."""
    limit = max(1, min(limit, 200))
    docs = repo.list_documents(tenant_id, limit=limit, status=DocumentStatus.ACTIVE)
    return [
        {
            "document_id": d.id,
            "title": d.title,
            "filename": d.original_filename,
            "document_date": _date_str(d.metadata.get("document_date")),
            "summary": d.metadata.get("summary"),
        }
        for d in docs
    ]


def aggregate_records(
    repo: RecordRepository,
    tenant_id: str,
    *,
    merchant: str | None = None,
    record_type: str | None = None,
    direction: str | None = None,
    currency: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Deterministic sum/count over the tenant's structured records (e.g. spend at a merchant)."""
    intent = AggregationIntent(
        operation="sum",
        merchant=merchant,
        record_type=record_type,
        direction=direction,
        currency=currency,
        date_from=_parse_date(date_from),
        date_to=_parse_date(date_to),
    )
    return repo.aggregate(tenant_id, intent).model_dump(mode="json")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _date_str(value: Any) -> str | None:
    return str(value) if value else None
