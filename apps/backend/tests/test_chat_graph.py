"""Multi-agent chat graph (ADR-0022 Phase 2c): scripted model + fake retrieve tool, no network."""

from __future__ import annotations

from typing import cast

from doktok_api.orchestration import run_graph
from doktok_contracts.media import AgentMessage, ToolCallTurn
from doktok_contracts.ports import ToolCallingChatModel
from doktok_contracts.schemas import Citation
from doktok_core.tools.base import Tool, ToolGateway, ToolRegistry, ToolResult
from pydantic import BaseModel


class _RetrieveArgs(BaseModel):
    query: str
    limit: int = 8


class _RetrieveTool:
    name = "retrieve_passages"
    description = "search"
    args_model: type[BaseModel] = _RetrieveArgs

    def run(self, tenant_id: str, args: _RetrieveArgs) -> ToolResult:
        return ToolResult(
            tool=self.name,
            summary=f"[1] evidence about {args.query}",
            citations=[Citation(index=1, document_id="d1", chunk_id="c1", snippet="evidence")],
        )


class ScriptedModel:
    def __init__(self, turns: list[ToolCallTurn]) -> None:
        self._turns = list(turns)
        self.calls = 0

    def chat_with_tools(
        self, messages: list[AgentMessage], tools: list[dict[str, object]]
    ) -> ToolCallTurn:
        self.calls += 1
        return self._turns.pop(0) if self._turns else ToolCallTurn(text="OK")


def _gateway() -> ToolGateway:
    return ToolGateway(ToolRegistry(cast("list[Tool]", [_RetrieveTool()])))


def _model(turns: list[ToolCallTurn]) -> ToolCallingChatModel:
    return cast(ToolCallingChatModel, ScriptedModel(turns))


def test_graph_gathers_then_answers_grounded() -> None:
    # researcher returns a final answer immediately; critic says OK -> done in one pass.
    model = _model(
        [ToolCallTurn(text="The rent is grounded in evidence [1]."), ToolCallTurn(text="OK")]
    )
    answer = run_graph("t", "what is the rent", model=model, gateway=_gateway(), tool_specs=[])
    assert answer.grounded
    assert "rent is grounded" in answer.answer
    # citations come from the gathered+merged evidence (the retrieve tool)
    assert answer.citations and answer.citations[0].document_id == "d1"


def test_graph_revises_once_on_critic_request() -> None:
    scripted = ScriptedModel(
        [
            ToolCallTurn(text="first draft"),
            ToolCallTurn(text="REVISE: cite the source"),
            ToolCallTurn(text="second draft [1]"),
            ToolCallTurn(text="OK"),
        ]
    )
    answer = run_graph(
        "t",
        "what is the rent",
        model=cast(ToolCallingChatModel, scripted),
        gateway=_gateway(),
        tool_specs=[],
    )
    assert answer.answer == "second draft [1]"  # the revised answer won
    assert scripted.calls == 4  # researcher, critic(REVISE), researcher, critic(OK)


def test_graph_stops_after_max_attempts() -> None:
    # critic always says REVISE; the graph must still terminate (bounded loop) after 2 attempts.
    scripted = ScriptedModel(
        [
            ToolCallTurn(text="draft 1"),
            ToolCallTurn(text="REVISE: more"),
            ToolCallTurn(text="draft 2"),
            ToolCallTurn(text="REVISE: more"),
        ]
    )
    answer = run_graph(
        "t",
        "what is the rent",
        model=cast(ToolCallingChatModel, scripted),
        gateway=_gateway(),
        tool_specs=[],
    )
    assert answer.answer == "draft 2"  # second (final) attempt, not looped forever
