"""Multi-agent chat graph (ADR-0022 Phase 2c): scripted model + fake retrieve tool, no network."""

from __future__ import annotations

from typing import cast

from doktok_api.orchestration import run_graph, run_graph_stream
from doktok_contracts.media import AgentMessage, ToolCallTurn
from doktok_contracts.ports import ToolCallingChatModel
from doktok_contracts.schemas import ChatEvent, Citation, TraceStep
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


def test_graph_stream_emits_steps_live_then_final() -> None:
    model = _model([ToolCallTurn(text="grounded answer [1]"), ToolCallTurn(text="OK")])
    events = list(
        run_graph_stream("t", "what is the rent", model=model, gateway=_gateway(), tool_specs=[])
    )
    types = [e.type for e in events]
    step_events = [e for e in events if e.type == "step"]
    step_kinds = [e.trace_step.kind for e in step_events if e.trace_step]
    # Stage heartbeats + substantive steps from all nodes.
    assert "stage" in step_kinds
    assert "plan" in step_kinds
    assert "retrieval" in step_kinds
    assert "draft" in step_kinds
    assert "critique" in step_kinds
    assert "verification" in step_kinds
    assert "finalize" in step_kinds
    # Final events must arrive in order.
    assert types[-3:] == ["token", "sources", "done"]
    token = next(e for e in events if e.type == "token")
    assert "grounded answer" in token.delta


# ---------------------------------------------------------------------------
# Richer trace enrichment tests (Phase 1 #495)
# ---------------------------------------------------------------------------


def _stream_events(
    question: str = "what is x", turns: list[ToolCallTurn] | None = None
) -> list[ChatEvent]:
    if turns is None:
        turns = [ToolCallTurn(text="answer [1]"), ToolCallTurn(text="OK")]
    model = _model(turns)
    return list(run_graph_stream("t", question, model=model, gateway=_gateway(), tool_specs=[]))


def _steps_by_kind(events: list[ChatEvent], kind: str) -> list[TraceStep]:
    return [
        e.trace_step
        for e in events
        if e.type == "step" and e.trace_step and e.trace_step.kind == kind
    ]


def test_graph_emits_plan_step_with_planner_role() -> None:
    events = _stream_events()
    plans = _steps_by_kind(events, "plan")
    assert plans, "expected a plan step"
    assert plans[0].role == "planner"


def test_graph_emits_retrieval_step_with_hit_count() -> None:
    events = _stream_events()
    retrievals = _steps_by_kind(events, "retrieval")
    assert retrievals, "expected a retrieval step"
    retrieval = retrievals[0]
    # The label mentions how many citations were retrieved.
    assert "Retrieved" in retrieval.label
    # The detail names the source.
    assert "passages" in retrieval.detail


def test_graph_emits_draft_step_with_researcher_role_and_attempt() -> None:
    events = _stream_events()
    drafts = _steps_by_kind(events, "draft")
    assert drafts, "expected a draft step"
    assert drafts[0].role == "researcher"
    assert drafts[0].attempt == 1


def test_graph_emits_draft_attempt_increments_on_revision() -> None:
    # Two researcher calls (one revision): attempt should be 1 then 2.
    scripted = ScriptedModel(
        [
            ToolCallTurn(text="first draft"),
            ToolCallTurn(text="REVISE: fix this"),
            ToolCallTurn(text="second draft [1]"),
            ToolCallTurn(text="OK"),
        ]
    )
    events = list(
        run_graph_stream(
            "t",
            "what is x",
            model=cast(ToolCallingChatModel, scripted),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    draft_steps = [
        e.trace_step
        for e in events
        if e.type == "step" and e.trace_step and e.trace_step.kind == "draft"
    ]
    assert len(draft_steps) == 2
    assert draft_steps[0].attempt == 1
    assert draft_steps[1].attempt == 2


def test_graph_emits_critique_and_verification_from_critic() -> None:
    events = _stream_events()
    critiques = _steps_by_kind(events, "critique")
    verifications = _steps_by_kind(events, "verification")
    assert critiques, "expected a critique step"
    assert critiques[0].role == "critic"
    assert verifications, "expected a verification step"
    assert verifications[0].role == "verifier"
    assert verifications[0].verdict == "pass"


def test_graph_emits_verification_verdict_revise_then_fail_on_max_attempts() -> None:
    # critic says REVISE twice -> first verification=revise, second verification=fail.
    scripted = ScriptedModel(
        [
            ToolCallTurn(text="draft 1"),
            ToolCallTurn(text="REVISE: more detail"),
            ToolCallTurn(text="draft 2"),
            ToolCallTurn(text="REVISE: still not good"),
        ]
    )
    events = list(
        run_graph_stream(
            "t",
            "what is x",
            model=cast(ToolCallingChatModel, scripted),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    verification_steps = [
        e.trace_step
        for e in events
        if e.type == "step" and e.trace_step and e.trace_step.kind == "verification"
    ]
    assert len(verification_steps) == 2
    assert verification_steps[0].verdict == "revise"
    assert verification_steps[1].verdict == "fail"


def test_graph_emits_stage_heartbeats_for_all_nodes() -> None:
    events = _stream_events()
    stage_labels = [s.label for s in _steps_by_kind(events, "stage")]
    # One stage heartbeat per node: planner, gather, researcher, critic, finalize.
    assert "Planning" in stage_labels
    assert "Gathering evidence" in stage_labels
    assert "Researching" in stage_labels
    assert "Reviewing" in stage_labels
    assert "Finalizing" in stage_labels


def test_graph_trace_steps_carry_iso8601_timestamps() -> None:
    from datetime import datetime

    events = _stream_events()
    step_events = [e for e in events if e.type == "step" and e.trace_step]
    for se in step_events:
        assert se.trace_step is not None
        assert se.trace_step.at is not None, f"step {se.trace_step.kind!r} is missing 'at'"
        datetime.fromisoformat(se.trace_step.at)  # raises if malformed
