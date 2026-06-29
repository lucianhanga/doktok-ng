"""Single-agent tool loop (ADR-0022 Phase 2b): scripted model + real gateway, no network."""

from __future__ import annotations

from typing import cast

from doktok_contracts.media import AgentMessage, LlmToolCall, ToolCallTurn
from doktok_contracts.ports import ToolCallingChatModel
from doktok_contracts.schemas import Citation
from doktok_core.agent.loop import run_agent, run_agent_stream
from doktok_core.tools.base import Tool, ToolGateway, ToolRegistry, ToolResult
from pydantic import BaseModel


class _CountArgs(BaseModel):
    entity: str | None = None


class _FakeCountTool:
    name = "count_documents"
    description = "count"
    args_model: type[BaseModel] = _CountArgs

    def run(self, tenant_id: str, args: _CountArgs) -> ToolResult:
        return ToolResult(
            tool=self.name,
            summary=f"57 documents mention {args.entity}.",
            data={"count": 57},
            citations=[Citation(index=1, document_id="d1", chunk_id="c1", snippet="evidence")],
        )


class ScriptedModel:
    """A ToolCallingChatModel that replays a fixed list of turns (then returns empty final text)."""

    def __init__(self, turns: list[ToolCallTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[tuple[list[AgentMessage], list[dict[str, object]]]] = []

    def chat_with_tools(
        self, messages: list[AgentMessage], tools: list[dict[str, object]]
    ) -> ToolCallTurn:
        self.calls.append((list(messages), list(tools)))
        return self._turns.pop(0) if self._turns else ToolCallTurn(text="done")


def _gateway() -> ToolGateway:
    return ToolGateway(ToolRegistry(cast("list[Tool]", [_FakeCountTool()])))


def _model(turns: list[ToolCallTurn]) -> ToolCallingChatModel:
    return cast(ToolCallingChatModel, ScriptedModel(turns))


def test_loop_calls_tool_then_answers_with_citations() -> None:
    model = _model(
        [
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
            ),
            ToolCallTurn(text="There are 57 documents about x [1]."),
        ]
    )
    gateway = _gateway()
    answer = run_agent("t", "how many x docs", model=model, gateway=gateway, tool_specs=[])
    assert answer.grounded
    assert "57" in answer.answer
    assert len(answer.citations) == 1 and answer.citations[0].document_id == "d1"


def test_loop_streams_step_events_per_tool_call() -> None:
    model = _model(
        [
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
            ),
            ToolCallTurn(text="answer [1]"),
        ]
    )
    events = list(run_agent_stream("t", "q", model=model, gateway=_gateway(), tool_specs=[]))
    types = [e.type for e in events]
    assert "step" in types and types[-1] == "done"
    step = next(e for e in events if e.type == "step")
    assert "count_documents" in step.delta


def test_loop_forces_close_when_budget_exhausted() -> None:
    # Every turn asks for a tool again and never finalizes -> the loop must force a closing answer.
    looping = ToolCallTurn(
        tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
    )
    scripted = ScriptedModel([looping, looping, ToolCallTurn(text="forced final answer")])
    answer = run_agent(
        "t",
        "q",
        model=cast(ToolCallingChatModel, scripted),
        gateway=_gateway(),
        tool_specs=[],
        max_iterations=2,
    )
    assert answer.answer == "forced final answer"


def test_loop_refusal_is_not_grounded() -> None:
    # A final answer that cites nothing (a refusal / "can't answer from the corpus") must report
    # grounded=False, so the refusal signal survives the agent path (eval refusal accuracy).
    model = _model([ToolCallTurn(text="I can't answer that from your documents.")])
    answer = run_agent("t", "capital of France?", model=model, gateway=_gateway(), tool_specs=[])
    assert not answer.grounded


def test_loop_cited_answer_is_grounded() -> None:
    model = _model([ToolCallTurn(text="The rent is 900 EUR [1].")])
    answer = run_agent("t", "rent?", model=model, gateway=_gateway(), tool_specs=[])
    assert answer.grounded


def test_loop_dedupes_citations_across_tool_calls() -> None:
    call = ToolCallTurn(
        tool_calls=[
            LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"}),
            LlmToolCall(id="2", name="count_documents", arguments={"entity": "y"}),
        ]
    )
    model = _model([call, ToolCallTurn(text="done [1]")])
    answer = run_agent("t", "q", model=model, gateway=_gateway(), tool_specs=[])
    # both tool calls return the same citation (d1/c1) -> deduped to one
    assert len(answer.citations) == 1
