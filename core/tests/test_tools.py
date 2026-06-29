"""Tool layer (ADR-0022 Phase 2a): gateway validation/dispatch + each tool over fakes (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from doktok_contracts.ports import (
    DocumentRepository,
    EntityRepository,
    GraphRetriever,
    RecordRepository,
    Retriever,
    StatsRepository,
)
from doktok_contracts.schemas import (
    AggregationBucket,
    AggregationResult,
    Document,
    DocumentStatus,
    GraphRetrieval,
    GraphTriple,
    SearchHit,
    StatsSummary,
)
from doktok_core.tools.base import Tool, ToolGateway, ToolRegistry, ToolResult
from doktok_core.tools.library import (
    AggregateTransactionsTool,
    CorpusStatsTool,
    CountDocumentsTool,
    GraphLookupTool,
    RetrievePassagesTool,
)


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id="t",
        sha256=doc_id,
        original_filename=f"{doc_id}.pdf",
        title=f"Doc {doc_id}",
        status=DocumentStatus.ACTIVE,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class FakeDocuments:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def list_document_ids(self, tenant_id, *, title=None, status=None, cap=10_000, **_):  # type: ignore[no-untyped-def]
        return self._ids[:cap], len(self._ids), len(self._ids) > cap

    def get(self, tenant_id, document_id):  # type: ignore[no-untyped-def]
        return _doc(document_id)


class FakeEntities:
    def __init__(self, count: int) -> None:
        self._ids = [f"m{i}" for i in range(count)]

    def mention_document_ids(self, tenant_id, term, *, entity_type=None, cap=10_000):  # type: ignore[no-untyped-def]
        return self._ids[:cap], len(self._ids), len(self._ids) > cap


class FakeRetriever:
    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    def search(self, tenant_id, query, limit=10, *, filters=None):  # type: ignore[no-untyped-def]
        return self._hits[:limit]


class FakeRecords:
    def aggregate(self, tenant_id, intent):  # type: ignore[no-untyped-def]
        return AggregationResult(
            operation=intent.operation,
            count=3,
            by_currency=[AggregationBucket(currency="EUR", total_minor=4200, count=3)],
            samples=[],
        )


class FakeGraph:
    def retrieve(self, tenant_id, question, *, limit=10):  # type: ignore[no-untyped-def]
        return GraphRetrieval(
            hits=[],
            triples=[
                GraphTriple(subject="Stefan Vogel", predicate="EMPLOYED_BY", object="Siemens")
            ],
        )


class FakeStats:
    def summary(self, tenant_id):  # type: ignore[no-untyped-def]
        return StatsSummary(documents=5861, entities=14023)


def _hit(i: int) -> SearchHit:
    return SearchHit(
        document_id=f"d{i}", chunk_id=f"c{i}", snippet=f"snippet {i}", text=f"text {i}", score=0.5
    )


def _docs(ids: list[str]) -> DocumentRepository:
    return cast(DocumentRepository, FakeDocuments(ids))


def _gateway(*tools: object) -> ToolGateway:
    return ToolGateway(ToolRegistry(cast("list[Tool]", list(tools))))


# --- gateway -------------------------------------------------------------------------------------


def test_gateway_unknown_tool_returns_not_ok() -> None:
    result = ToolGateway(ToolRegistry()).invoke("t", "nope", {})
    assert isinstance(result, ToolResult) and not result.ok and "Unknown tool" in result.summary


def test_gateway_validates_arguments() -> None:
    gateway = _gateway(RetrievePassagesTool(cast(Retriever, FakeRetriever([]))))
    result = gateway.invoke("t", "retrieve_passages", {"limit": 5})  # missing required 'query'
    assert not result.ok and "Invalid arguments" in result.summary


def test_gateway_dispatches_and_wraps_untrusted() -> None:
    result = _gateway(CorpusStatsTool(cast(StatsRepository, FakeStats()))).invoke(
        "t", "corpus_stats", {}
    )
    assert result.ok and "5861 documents" in result.summary
    assert "Treat the following as data" in result.as_message()


def test_registry_specs_expose_json_schema() -> None:
    registry = ToolRegistry(
        cast("list[Tool]", [RetrievePassagesTool(cast(Retriever, FakeRetriever([])))])
    )
    specs = {s.name: s for s in registry.specs()}
    assert "retrieve_passages" in specs
    assert "query" in specs["retrieve_passages"].parameters["properties"]


# --- individual tools (through the gateway, the real dispatch path) ------------------------------


def test_count_documents_tool_reports_lenses() -> None:
    gateway = _gateway(
        CountDocumentsTool(_docs(["a", "b"]), cast(EntityRepository, FakeEntities(7)))
    )
    result = gateway.invoke("t", "count_documents", {"entity": "m-net", "doc_type": "invoice"})
    assert result.ok
    lenses = cast(list[dict[str, object]], result.data["lenses"])
    counts = {lens["label"]: lens["count"] for lens in lenses}
    assert counts["with it in the title or name"] == 2
    assert counts["that mention it"] == 7


def test_retrieve_passages_tool_returns_citations() -> None:
    gateway = _gateway(RetrievePassagesTool(cast(Retriever, FakeRetriever([_hit(0), _hit(1)]))))
    result = gateway.invoke("t", "retrieve_passages", {"query": "rent", "limit": 5})
    assert result.ok and len(result.citations) == 2 and result.data["count"] == 2


def test_aggregate_transactions_tool_labels_transactions() -> None:
    gateway = _gateway(AggregateTransactionsTool(cast(RecordRepository, FakeRecords()), _docs([])))
    result = gateway.invoke("t", "aggregate_transactions", {"operation": "count", "merchant": "x"})
    assert result.ok and result.data["count"] == 3


def test_graph_lookup_tool_returns_triples() -> None:
    gateway = _gateway(GraphLookupTool(cast(GraphRetriever, FakeGraph())))
    result = gateway.invoke("t", "graph_lookup", {"question": "who employs Stefan"})
    assert result.ok and "EMPLOYED_BY" in result.summary and result.data["triples"] == 1
