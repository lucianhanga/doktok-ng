"""Agentic-chat tool layer (ADR-0022 Phase 2a): typed, read-only tools over the existing ports,
dispatched through a single validating gateway. Framework-free - no LangGraph."""

from doktok_core.tools.base import Tool, ToolGateway, ToolRegistry, ToolResult, ToolSpec
from doktok_core.tools.library import (
    AggregateTransactionsTool,
    CorpusStatsTool,
    CountDocumentsTool,
    GraphLookupTool,
    ListCategoriesTool,
    RetrievePassagesTool,
    build_default_registry,
)

__all__ = [
    "AggregateTransactionsTool",
    "CorpusStatsTool",
    "CountDocumentsTool",
    "GraphLookupTool",
    "ListCategoriesTool",
    "RetrievePassagesTool",
    "Tool",
    "ToolGateway",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "build_default_registry",
]
