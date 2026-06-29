"""The multi-agent chat graph (ADR-0022 Phase 2c): planner -> gather -> merge -> researcher ->
critic -> finalize, with a bounded critic->researcher revise loop.

LangGraph owns only the topology + typed state. Source selection is deterministic (the existing
cheap gates, no extra LLM call); ``gather`` runs the chosen tools through the gateway; ``merge``
fuses their citations by cross-source RRF; ``researcher`` is the Phase-2b single-agent loop grounded
on the merged evidence; ``critic`` does one verify pass and can ask for a single revision. The model
and gateway are doktok ports, called directly - langgraph never sees them as its own abstractions.
"""

from __future__ import annotations

import operator
from collections.abc import Iterator, Sequence
from typing import Annotated, TypedDict

from doktok_contracts.media import AgentMessage
from doktok_contracts.ports import ToolCallingChatModel
from doktok_contracts.schemas import ChatEvent, ChatTurn, Citation, RagAnswer
from doktok_core.agent import evidence_block, merge_evidence, run_agent
from doktok_core.aggregation.counting import parse_count_intent
from doktok_core.knowledge_graph.retrieval import looks_relational
from doktok_core.tools.base import ToolGateway, ToolResult, ToolSpec
from langgraph.graph import END, START, StateGraph

_MAX_ATTEMPTS = 2
_RETRIEVE_LIMIT = 8

_CRITIC_PROMPT = (
    "You are reviewing an assistant's answer about the user's documents against the evidence it "
    "was given. If the answer is supported by the evidence and addresses the question, reply with "
    "exactly OK. Otherwise reply 'REVISE: <one sentence on what to fix>'. Reply with nothing "
    "else.\n\nQuestion: {question}\n\nEvidence:\n{evidence}\n\nAnswer:\n{answer}"
)


class _State(TypedDict, total=False):
    question: str
    tenant_id: str
    history: list[ChatTurn]
    evidence: list[Citation]
    evidence_text: str
    answer: str
    citations: list[Citation]
    grounded: bool
    attempts: int
    critique: str
    trace: Annotated[list[str], operator.add]


def _plan(question: str) -> list[tuple[str, dict[str, object]]]:
    """Deterministically choose the retrieval sources (no LLM): always the hybrid passages, plus a
    graph lookup for relational questions and a document count for count questions."""
    plan: list[tuple[str, dict[str, object]]] = [
        ("retrieve_passages", {"query": question, "limit": _RETRIEVE_LIMIT})
    ]
    if looks_relational(question):
        plan.append(("graph_lookup", {"question": question}))
    count_intent = parse_count_intent(question)
    if count_intent is not None:
        plan.append(
            ("count_documents", {"entity": count_intent.entity, "doc_type": count_intent.doc_type})
        )
    return plan


def build_chat_graph(
    model: ToolCallingChatModel, gateway: ToolGateway, tool_specs: Sequence[ToolSpec]
) -> object:
    """Compile the LangGraph chat graph closed over the per-turn model + gateway + tool specs."""

    def planner(state: _State) -> _State:
        return {"attempts": 0, "trace": ["Planning the approach"]}

    def gather(state: _State) -> _State:
        question = state["question"]
        plan = _plan(question)
        # The gateway here is tenant-bound (see _TenantGateway), so the tenant arg is ignored.
        results: list[ToolResult] = [gateway.invoke("", name, args) for name, args in plan]
        evidence = merge_evidence([r.citations for r in results if r.ok], limit=_RETRIEVE_LIMIT)
        summaries = "\n\n".join(r.as_message() for r in results if r.ok and r.summary)
        names = ", ".join(name for name, _ in plan)
        return {
            "evidence": evidence,
            "evidence_text": summaries,
            "trace": [f"Gathering from: {names}"],
        }

    def researcher(state: _State) -> _State:
        question = state["question"]
        block = evidence_block(state.get("evidence", []))
        parts = [p for p in (state.get("evidence_text", ""), block) if p]
        critique = state.get("critique", "")
        if critique:
            parts.append(f"A reviewer asked you to revise: {critique}")
        grounding = [ChatTurn(role="system", content="\n\n".join(parts))] if parts else []
        history = [*grounding, *state.get("history", [])]
        answer = run_agent(
            state.get("tenant_id", ""),
            question,
            model=model,
            gateway=gateway,
            tool_specs=tool_specs,
            history=history,
        )
        citations = answer.citations or state.get("evidence", [])
        return {
            "answer": answer.answer,
            "citations": citations,
            "grounded": answer.grounded,
            "attempts": state.get("attempts", 0) + 1,
            "trace": ["Researching the answer"],
        }

    def critic(state: _State) -> _State:
        prompt = _CRITIC_PROMPT.format(
            question=state["question"],
            evidence=evidence_block(state.get("evidence", [])) or "(none)",
            answer=state.get("answer", ""),
        )
        turn = model.chat_with_tools([AgentMessage(role="user", content=prompt)], [])
        verdict = turn.text.strip()
        critique = (
            "" if verdict.upper().startswith("OK") else verdict.removeprefix("REVISE:").strip()
        )
        return {"critique": critique, "trace": ["Reviewing the answer"]}

    def _route_after_critic(state: _State) -> str:
        if state.get("critique") and state.get("attempts", 0) < _MAX_ATTEMPTS:
            return "researcher"
        return END

    def finalize(state: _State) -> _State:
        return {"trace": ["Finalizing"]}

    graph = StateGraph(_State)
    graph.add_node("planner", planner)
    graph.add_node("gather", gather)
    graph.add_node("researcher", researcher)
    graph.add_node("critic", critic)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "gather")
    graph.add_edge("gather", "researcher")
    graph.add_edge("researcher", "critic")
    graph.add_conditional_edges(
        "critic", _route_after_critic, {"researcher": "researcher", END: "finalize"}
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def run_graph_stream(
    tenant_id: str,
    question: str,
    *,
    model: ToolCallingChatModel,
    gateway: ToolGateway,
    tool_specs: Sequence[ToolSpec],
    history: Sequence[ChatTurn] = (),
) -> Iterator[ChatEvent]:
    """Run the graph and replay it as the chat event stream (step trace, then token/sources/done).
    The graph runs to completion first (not token-streamed); the activity trace shows the path."""
    # The gateway is tenant-bound at call time; bind the tenant by wrapping invoke for this run.
    bound = _TenantGateway(gateway, tenant_id)
    compiled = build_chat_graph(model, bound, tool_specs)  # type: ignore[arg-type]
    final: _State = compiled.invoke(  # type: ignore[attr-defined]
        {"question": question, "history": list(history), "tenant_id": tenant_id, "attempts": 0}
    )
    for label in final.get("trace", []):
        yield ChatEvent(type="step", delta=label)
    yield ChatEvent(type="token", delta=final.get("answer", ""))
    yield ChatEvent(type="sources", citations=final.get("citations") or final.get("evidence") or [])
    yield ChatEvent(type="done", grounded=bool(final.get("grounded")))


def run_graph(
    tenant_id: str,
    question: str,
    *,
    model: ToolCallingChatModel,
    gateway: ToolGateway,
    tool_specs: Sequence[ToolSpec],
    history: Sequence[ChatTurn] = (),
) -> RagAnswer:
    """Non-streaming: drain ``run_graph_stream`` into a RagAnswer (the JSON endpoint)."""
    answer = ""
    citations: list[Citation] = []
    grounded = False
    for event in run_graph_stream(
        tenant_id, question, model=model, gateway=gateway, tool_specs=tool_specs, history=history
    ):
        if event.type == "token":
            answer = event.delta
        elif event.type == "sources":
            citations = event.citations
        elif event.type == "done":
            grounded = event.grounded
    return RagAnswer(
        answer=answer or "I could not find enough evidence to answer that.",
        citations=citations,
        grounded=grounded,
    )


class _TenantGateway:
    """Binds a tenant id to a ToolGateway so the graph nodes can call ``invoke(name, args)`` without
    threading the tenant through LangGraph state into every node."""

    def __init__(self, gateway: ToolGateway, tenant_id: str) -> None:
        self._gateway = gateway
        self._tenant_id = tenant_id

    def invoke(self, _tenant: str, name: str, raw_args: dict[str, object]) -> ToolResult:
        return self._gateway.invoke(self._tenant_id, name, raw_args)
