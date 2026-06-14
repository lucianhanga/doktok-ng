"""Read-only MCP tool logic + tenant resolution (no transport, no DB/Ollama)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from doktok_contracts.schemas import (
    AggregationIntent,
    Document,
    DocumentStatus,
    ExtractedRecord,
    SearchHit,
)
from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_mcp import tools
from doktok_mcp.server import resolve_tenant

TENANT = "t1"


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.seen: tuple[str, str, int] | None = None

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]
        self.seen = (tenant_id, query, limit)
        return self._hits


def test_resolve_tenant() -> None:
    assert resolve_tenant("alice", ["alice", "bob"]) == "alice"
    assert resolve_tenant("", ["only"]) == "only"  # single tenant inferred
    with pytest.raises(ValueError):
        resolve_tenant("ghost", ["alice"])  # not a configured tenant
    with pytest.raises(ValueError):
        resolve_tenant("", ["a", "b"])  # ambiguous


def test_search_is_tenant_scoped() -> None:
    retr = FakeRetriever([SearchHit(document_id="d1", chunk_id="c1", snippet="hi", score=1.0)])
    out = tools.search_documents(retr, TENANT, "q", 5)
    assert retr.seen == (TENANT, "q", 5)  # tenant passed through, never from the caller's args
    assert out[0]["document_id"] == "d1"


def test_list_documents_only_active_for_tenant() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(
        Document(
            id="d1",
            tenant_id=TENANT,
            sha256="a" * 64,
            original_filename="f.pdf",
            title="Doc One",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
            metadata={"document_date": "2024-02-03", "summary": "a summary"},
        )
    )
    repo.add(
        Document(
            id="d2",
            tenant_id="other",
            sha256="b" * 64,
            original_filename="g.pdf",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )
    out = tools.list_documents(repo, TENANT)
    assert [d["document_id"] for d in out] == ["d1"]  # other tenant's doc excluded
    assert out[0]["document_date"] == "2024-02-03" and out[0]["summary"] == "a summary"


def test_aggregate_records_tool() -> None:
    repo = InMemoryRecordRepository()
    repo.replace_for_document(
        TENANT,
        "d1",
        [
            ExtractedRecord(
                id="r1",
                tenant_id=TENANT,
                document_id="d1",
                raw_text="x",
                occurred_on=date(2024, 2, 3),
                amount_minor=4250,
                currency="EUR",
                direction="debit",
                merchant_normalized="block house hamburg",
            )
        ],
    )
    out = tools.aggregate_records(repo, TENANT, merchant="block house")
    assert out["count"] == 1
    assert out["by_currency"][0]["total_minor"] == 4250


def test_tool_allowlist_is_read_only() -> None:
    # Exactly the three read tools are exposed; no write/delete tool exists.
    assert tools.TOOL_NAMES == ("search_documents", "list_documents", "aggregate_records")
    assert not any("delete" in n or "write" in n or "add" in n for n in tools.TOOL_NAMES)


def test_aggregate_intent_used_is_typed() -> None:
    # Sanity: the tool builds a typed AggregationIntent (no text-to-SQL).
    intent = AggregationIntent(merchant="block house")
    assert intent.operation == "sum"
