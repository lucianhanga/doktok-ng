"""LangGraph multi-agent chat orchestration (ADR-0022 Phase 2c).

Adapter-only: LangGraph supplies the graph topology + typed state here; the model and the tool
gateway stay on doktok_core / doktok_contracts ports and are called directly. core/contracts never
import langgraph (enforced by lint-imports)."""

from doktok_api.orchestration.graph import run_graph, run_graph_stream

__all__ = ["run_graph", "run_graph_stream"]
