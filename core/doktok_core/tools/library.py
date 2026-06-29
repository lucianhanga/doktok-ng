"""The concrete tool set for agentic chat (ADR-0022 Phase 2a).

Each tool is a thin wrapper over an existing port, deterministic and read-only. The headline one is
``count_documents`` - exact SQL counts so "how many X" is never the LLM counting a retrieval window.
``build_default_registry`` wires them with the tenant's repositories (called from composition).
"""

from __future__ import annotations

from datetime import date

from doktok_contracts.ports import (
    CategoryRepository,
    DocumentRepository,
    EntityRepository,
    GraphRetriever,
    RecordRepository,
    Retriever,
    StatsRepository,
)
from doktok_contracts.schemas import AggregationIntent, Citation
from pydantic import BaseModel, Field

from doktok_core.aggregation.counting import CountIntent, count_answer, count_documents
from doktok_core.aggregation.router import aggregation_answer
from doktok_core.tools.base import Tool, ToolRegistry, ToolResult


class _NoArgs(BaseModel):
    pass


# --- count_documents -----------------------------------------------------------------------------


class CountArgs(BaseModel):
    entity: str | None = Field(
        default=None,
        description="Entity/name to count documents about, e.g. 'm-net'. Null = the whole corpus.",
    )
    doc_type: str | None = Field(
        default=None, description="Document type the user named, e.g. 'invoice' (informational)."
    )


class CountDocumentsTool:
    name = "count_documents"
    description = (
        "Count documents in the corpus, optionally about a named entity. Use for 'how many "
        "documents/invoices/letters ...'. Returns EXACT counts (by title and by mention), never an "
        "estimate. Counts documents, not transactions."
    )
    args_model: type[BaseModel] = CountArgs

    def __init__(self, documents: DocumentRepository, entities: EntityRepository) -> None:
        self._documents = documents
        self._entities = entities

    def run(self, tenant_id: str, args: CountArgs) -> ToolResult:
        intent = CountIntent(entity=args.entity, doc_type=args.doc_type)
        report = count_documents(
            tenant_id, intent, documents=self._documents, entities=self._entities
        )
        answer = count_answer(report, self._documents, tenant_id)
        data: dict[str, object] = {
            "entity": report.entity,
            "doc_type": report.doc_type,
            "lenses": [
                {"label": lens.label, "count": lens.count, "truncated": lens.truncated}
                for lens in report.lenses
            ],
        }
        if answer is None:
            return ToolResult(self.name, summary="No matching documents found.", data=data)
        return ToolResult(self.name, summary=answer.answer, data=data, citations=answer.citations)


# --- retrieve_passages ---------------------------------------------------------------------------


class RetrieveArgs(BaseModel):
    query: str = Field(description="The search query to find supporting passages for.")
    limit: int = Field(default=8, ge=1, le=20, description="Maximum passages to return.")


class RetrievePassagesTool:
    name = "retrieve_passages"
    description = (
        "Semantic + keyword search over the documents; returns the most relevant passages with "
        "citations. Use to find evidence to answer a content question."
    )
    args_model: type[BaseModel] = RetrieveArgs

    def __init__(self, retriever: Retriever) -> None:
        self._retriever = retriever

    def run(self, tenant_id: str, args: RetrieveArgs) -> ToolResult:
        hits = self._retriever.search(tenant_id, args.query, args.limit)
        if not hits:
            return ToolResult(self.name, summary=f"No passages found for '{args.query}'.")
        citations = [
            Citation(
                index=i + 1,
                document_id=h.document_id,
                chunk_id=h.chunk_id,
                original_filename=h.original_filename,
                title=h.title,
                page_start=h.page_start,
                page_end=h.page_end,
                snippet=h.snippet,
            )
            for i, h in enumerate(hits)
        ]
        summary = "\n".join(f"[{i + 1}] {h.snippet}" for i, h in enumerate(hits))
        return ToolResult(
            self.name, summary=summary, data={"count": len(hits)}, citations=citations
        )


# --- aggregate_transactions ----------------------------------------------------------------------


class AggregateArgs(BaseModel):
    operation: str = Field(
        default="sum", description="'sum' of amounts or 'count' of transactions."
    )
    merchant: str | None = Field(default=None, description="Merchant/payee name, fuzzy-matched.")
    direction: str | None = Field(default=None, description="'debit' (spend) or 'credit' (income).")
    currency: str | None = Field(default=None, description="ISO 4217 currency, exact.")
    date_from: date | None = None
    date_to: date | None = None


class AggregateTransactionsTool:
    name = "aggregate_transactions"
    description = (
        "Sum or count financial transactions (bank/card-statement line-items), optionally filtered "
        "by merchant/direction/currency/date. NOTE: counts transactions, NOT documents - for a "
        "document count use count_documents."
    )
    args_model: type[BaseModel] = AggregateArgs

    def __init__(self, records: RecordRepository, documents: DocumentRepository) -> None:
        self._records = records
        self._documents = documents

    def run(self, tenant_id: str, args: AggregateArgs) -> ToolResult:
        intent = AggregationIntent(
            operation="count" if args.operation == "count" else "sum",
            merchant=args.merchant,
            direction=args.direction if args.direction in ("debit", "credit") else None,
            currency=args.currency,
            date_from=args.date_from,
            date_to=args.date_to,
        )
        result = self._records.aggregate(tenant_id, intent)
        answer = aggregation_answer(intent, result, self._documents, tenant_id)
        return ToolResult(
            self.name,
            summary=answer.answer,
            data={"count": result.count, "operation": result.operation},
            citations=answer.citations,
        )


# --- graph_lookup --------------------------------------------------------------------------------


class GraphArgs(BaseModel):
    question: str = Field(description="The relational question, e.g. 'who is Stefan employed by'.")
    limit: int = Field(default=10, ge=1, le=20)


class GraphLookupTool:
    name = "graph_lookup"
    description = (
        "Look up relationships between entities in the knowledge graph (who is X employed by, how "
        "is X connected to Y). Returns relationship triples plus the supporting passages."
    )
    args_model: type[BaseModel] = GraphArgs

    def __init__(self, graph_retriever: GraphRetriever) -> None:
        self._graph = graph_retriever

    def run(self, tenant_id: str, args: GraphArgs) -> ToolResult:
        retrieval = self._graph.retrieve(tenant_id, args.question, limit=args.limit)
        if not retrieval.hits and not retrieval.triples:
            return ToolResult(self.name, summary="No relationships found in the knowledge graph.")
        triples = [f"{t.subject} {t.predicate} {t.object}" for t in retrieval.triples]
        citations = [
            Citation(
                index=i + 1,
                document_id=h.document_id,
                chunk_id=h.chunk_id,
                original_filename=h.original_filename,
                title=h.title,
                page_start=h.page_start,
                page_end=h.page_end,
                snippet=h.snippet,
            )
            for i, h in enumerate(retrieval.hits)
        ]
        parts: list[str] = []
        if triples:
            parts.append("Relationships:\n" + "\n".join(triples))
        if citations:
            parts.append("\n".join(f"[{c.index}] {c.snippet}" for c in citations))
        return ToolResult(
            self.name,
            summary="\n\n".join(parts),
            data={"triples": len(triples), "hits": len(citations)},
            citations=citations,
        )


# --- corpus_stats --------------------------------------------------------------------------------


class CorpusStatsTool:
    name = "corpus_stats"
    description = (
        "Overall corpus statistics: total documents and distinct entities. Use for 'how many "
        "documents do I have in total'."
    )
    args_model: type[BaseModel] = _NoArgs

    def __init__(self, stats: StatsRepository) -> None:
        self._stats = stats

    def run(self, tenant_id: str, args: _NoArgs) -> ToolResult:
        summary = self._stats.summary(tenant_id)
        return ToolResult(
            self.name,
            summary=f"{summary.documents} documents and {summary.entities} distinct entities.",
            data={"documents": summary.documents, "entities": summary.entities},
        )


# --- list_categories -----------------------------------------------------------------------------


class ListCategoriesTool:
    name = "list_categories"
    description = "List the document categories and how many documents each one has."
    args_model: type[BaseModel] = _NoArgs

    def __init__(self, categories: CategoryRepository) -> None:
        self._categories = categories

    def run(self, tenant_id: str, args: _NoArgs) -> ToolResult:
        cats = self._categories.list_summary(tenant_id)
        if not cats:
            return ToolResult(self.name, summary="No categories are configured.")
        summary = "; ".join(f"{c.name} ({c.document_count})" for c in cats)
        return ToolResult(
            self.name,
            summary=summary,
            data={"categories": [{"name": c.name, "count": c.document_count} for c in cats]},
        )


def build_default_registry(
    *,
    documents: DocumentRepository,
    entities: EntityRepository,
    retriever: Retriever,
    records: RecordRepository,
    graph_retriever: GraphRetriever,
    stats: StatsRepository,
    categories: CategoryRepository,
) -> ToolRegistry:
    """Wire the standard read-only tool set with a tenant's repositories (from composition)."""
    tools: list[Tool] = [
        CountDocumentsTool(documents, entities),
        RetrievePassagesTool(retriever),
        AggregateTransactionsTool(records, documents),
        GraphLookupTool(graph_retriever),
        CorpusStatsTool(stats),
        ListCategoriesTool(categories),
    ]
    return ToolRegistry(tools)
