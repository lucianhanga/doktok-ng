"""Single-agent tool loop (ADR-0022 Phase 2b): scripted model + real gateway, no network."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from doktok_contracts.media import AgentMessage, ChatChunk, LlmToolCall, LlmUsage, ToolCallTurn
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


class StreamingScriptedModel(ScriptedModel):
    """A ScriptedModel that also implements ``stream_reply`` for the streaming final-answer path.

    ``stream_chunks`` are the ``ChatChunk``s that ``stream_reply`` yields; ``stream_usage`` is the
    ``LlmUsage`` that ``get_last_usage()`` returns after the stream.
    """

    def __init__(
        self,
        turns: list[ToolCallTurn],
        stream_chunks: list[ChatChunk],
        stream_usage: LlmUsage | None = None,
        stream_raises: bool = False,
    ) -> None:
        super().__init__(turns)
        self._stream_chunks = stream_chunks
        self._stream_usage = stream_usage
        self._stream_raises = stream_raises
        self._stream_messages: list[AgentMessage] | None = None

    def stream_reply(
        self, messages: list[AgentMessage], *, think: bool | None = None
    ) -> Iterator[ChatChunk]:
        self._stream_messages = list(messages)
        if self._stream_raises:
            raise RuntimeError("stream_reply deliberately failed")
        yield from self._stream_chunks

    def get_last_usage(self) -> LlmUsage | None:
        return self._stream_usage


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
    kinds = [e.trace_step.kind for e in events if e.type == "step" and e.trace_step]
    # the chronological trace: understand -> the count tool -> compose
    assert kinds[0] == "understand"
    assert "count" in kinds  # the count_documents tool, by kind
    assert kinds[-1] == "compose"
    count = next(
        e.trace_step
        for e in events
        if e.type == "step" and e.trace_step
        if e.trace_step.kind == "count"
    )
    assert count.label == "Counting matching documents"


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


def test_loop_aggregates_usage_into_metrics() -> None:
    from doktok_contracts.media import LlmUsage

    model = _model(
        [
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})],
                usage=LlmUsage(prompt_tokens=100, answer_tokens=10, reasoning_tokens=0),
            ),
            ToolCallTurn(
                text="answer [1]",
                usage=LlmUsage(prompt_tokens=200, answer_tokens=40, reasoning_tokens=5),
            ),
        ]
    )
    answer = run_agent("t", "q", model=model, gateway=_gateway(), tool_specs=[])
    assert answer.metrics is not None
    assert answer.metrics.prompt_tokens == 300  # summed across both model calls
    assert answer.metrics.answer_tokens == 50
    assert answer.metrics.reasoning_tokens == 5


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


def test_tool_step_summarizes_args_in_detail() -> None:
    from datetime import datetime

    from doktok_core.agent.trace import tool_step

    s = tool_step("retrieve_passages", {"query": "m-net invoices", "k": 8})
    assert s.kind == "retrieve"
    assert "query: m-net invoices" in s.detail and "k: 8" in s.detail
    # No args -> empty detail (back-compat for callers that don't pass args, e.g. the graph).
    assert tool_step("retrieve_passages").detail == ""
    # Each step carries an ISO-8601 UTC emit timestamp (per-step time in the activity timeline).
    assert s.at is not None
    datetime.fromisoformat(s.at)  # parseable; raises if malformed


# ---------------------------------------------------------------------------
# Streaming final-answer tests (issue #485)
# ---------------------------------------------------------------------------


def test_stream_reply_yields_multiple_token_events() -> None:
    # When the model implements stream_reply, the final answer arrives as multiple token events
    # rather than one blob - the UI can render words as they arrive.
    chunks = [
        ChatChunk(kind="answer", text="There are "),
        ChatChunk(kind="answer", text="57 documents "),
        ChatChunk(kind="answer", text="about x [1]."),
    ]
    streaming_model = StreamingScriptedModel(
        turns=[
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
            ),
            ToolCallTurn(text="fallback [1]"),
        ],
        stream_chunks=chunks,
    )
    events = list(
        run_agent_stream(
            "t",
            "q",
            model=cast(ToolCallingChatModel, streaming_model),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    token_events = [e for e in events if e.type == "token"]
    assert len(token_events) == 3, "expected one token event per chunk"
    assert token_events[0].delta == "There are "
    assert token_events[1].delta == "57 documents "
    assert token_events[2].delta == "about x [1]."
    # run_agent accumulates all deltas into the full answer
    answer = run_agent(
        "t",
        "q",
        model=cast(
            ToolCallingChatModel,
            StreamingScriptedModel(
                turns=[
                    ToolCallTurn(
                        tool_calls=[
                            LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})
                        ]
                    ),
                    ToolCallTurn(text="fallback [1]"),
                ],
                stream_chunks=chunks,
            ),
        ),
        gateway=_gateway(),
        tool_specs=[],
    )
    assert answer.answer == "There are 57 documents about x [1]."
    assert answer.grounded


def test_stream_reply_yields_reasoning_events_before_tokens() -> None:
    # Reasoning chunks must appear as ChatEvent(type="reasoning") events, interleaved with tokens
    # in the order the model emitted them.
    chunks = [
        ChatChunk(kind="reasoning", text="Let me count..."),
        ChatChunk(kind="answer", text="57 documents [1]."),
    ]
    streaming_model = StreamingScriptedModel(
        turns=[ToolCallTurn(text="fallback")],
        stream_chunks=chunks,
    )
    events = list(
        run_agent_stream(
            "t",
            "q",
            model=cast(ToolCallingChatModel, streaming_model),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    reasoning_events = [e for e in events if e.type == "reasoning"]
    token_events = [e for e in events if e.type == "token"]
    assert len(reasoning_events) == 1 and reasoning_events[0].delta == "Let me count..."
    assert len(token_events) == 1 and token_events[0].delta == "57 documents [1]."
    # The done event reflects the accumulated answer (only token chunks count for grounding).
    done_event = next(e for e in events if e.type == "done")
    assert done_event.grounded


def test_stream_reply_fallback_when_raises() -> None:
    # When stream_reply raises, the loop falls back to the blocking path and emits a single token
    # event with the full answer from _blocking_final (the natural-exit turn's text).
    streaming_model = StreamingScriptedModel(
        turns=[ToolCallTurn(text="blocking answer [1]")],
        stream_chunks=[],
        stream_raises=True,
    )
    events = list(
        run_agent_stream(
            "t",
            "q",
            model=cast(ToolCallingChatModel, streaming_model),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    token_events = [e for e in events if e.type == "token"]
    assert len(token_events) == 1, "fallback must yield exactly one token event"
    assert token_events[0].delta == "blocking answer [1]"
    done_event = next(e for e in events if e.type == "done")
    assert done_event.grounded


def test_stream_reply_fallback_for_model_without_stream_reply() -> None:
    # A model that only implements chat_with_tools (no stream_reply) must work unchanged:
    # the loop uses _blocking_final and yields a single token event.
    model = _model(
        [
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
            ),
            ToolCallTurn(text="answer via blocking [1]"),
        ]
    )
    events = list(run_agent_stream("t", "q", model=model, gateway=_gateway(), tool_specs=[]))
    token_events = [e for e in events if e.type == "token"]
    assert len(token_events) == 1
    assert "blocking [1]" in token_events[0].delta


def test_stream_reply_accumulates_usage_from_stream() -> None:
    # Usage from the streaming call is collected via get_last_usage() and added to the metrics.
    stream_usage = LlmUsage(prompt_tokens=50, answer_tokens=15, reasoning_tokens=3)
    streaming_model = StreamingScriptedModel(
        turns=[
            ToolCallTurn(
                tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})],
                usage=LlmUsage(prompt_tokens=100, answer_tokens=10, reasoning_tokens=0),
            ),
            ToolCallTurn(text="not used"),
        ],
        stream_chunks=[ChatChunk(kind="answer", text="57 docs [1].")],
        stream_usage=stream_usage,
    )
    answer = run_agent(
        "t",
        "q",
        model=cast(ToolCallingChatModel, streaming_model),
        gateway=_gateway(),
        tool_specs=[],
    )
    assert answer.metrics is not None
    # prompt_tokens from the tool-decision turn + stream prompt_tokens
    assert answer.metrics.prompt_tokens == 100 + 50
    assert answer.metrics.reasoning_tokens == 0 + 3


def test_stream_reply_done_grounded_from_accumulated_answer() -> None:
    # The grounded flag is derived from the FULL accumulated streamed text (all deltas joined),
    # not just the last delta.
    chunks = [
        ChatChunk(kind="answer", text="The answer is "),
        ChatChunk(kind="answer", text="in document "),
        ChatChunk(kind="answer", text="[1]."),
    ]
    streaming_model = StreamingScriptedModel(
        turns=[ToolCallTurn(text="fallback")],
        stream_chunks=chunks,
    )
    events = list(
        run_agent_stream(
            "t",
            "q",
            model=cast(ToolCallingChatModel, streaming_model),
            gateway=_gateway(),
            tool_specs=[],
        )
    )
    done_event = next(e for e in events if e.type == "done")
    # [1] appears in the last delta - the join must catch it
    assert done_event.grounded


def test_stream_reply_forced_close_uses_stream_reply() -> None:
    # When the budget is exhausted, stream_reply must still be called (with the close prompt
    # appended to messages) rather than the old blocking chat_with_tools(messages, []).
    looping = ToolCallTurn(
        tool_calls=[LlmToolCall(id="1", name="count_documents", arguments={"entity": "x"})]
    )
    streaming_model = StreamingScriptedModel(
        turns=[looping, looping],
        stream_chunks=[ChatChunk(kind="answer", text="forced streamed answer [1].")],
    )
    answer = run_agent(
        "t",
        "q",
        model=cast(ToolCallingChatModel, streaming_model),
        gateway=_gateway(),
        tool_specs=[],
        max_iterations=2,
    )
    assert answer.answer == "forced streamed answer [1]."
    assert answer.grounded
    # stream_reply must have been called with the close prompt as the last user message
    assert streaming_model._stream_messages is not None
    last_msg = streaming_model._stream_messages[-1]
    assert last_msg.role == "user"
    from doktok_core.agent.loop import _CLOSE_PROMPT

    assert last_msg.content == _CLOSE_PROMPT
