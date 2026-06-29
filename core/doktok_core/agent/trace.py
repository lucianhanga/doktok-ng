"""Reusable chronological activity-trace steps for chat (ADR-0022).

A single mechanism emitted by BOTH the single-agent loop and the multi-agent graph, so the UI
renders one consistent trace ("understanding → searching → knowledge graph → composing"). Each step
is a typed ``TraceStep`` (kind + human label); ``step_event`` wraps it as a ``ChatEvent`` (carrying
the label in ``delta`` too, for any legacy delta-only consumer).
"""

from __future__ import annotations

from doktok_contracts.schemas import ChatEvent, TraceStep

# Tool name -> (kind, human label). Kind drives the per-step icon/colour in the UI.
_TOOL_TRACE: dict[str, tuple[str, str]] = {
    "retrieve_passages": ("retrieve", "Searching your documents"),
    "graph_lookup": ("graph", "Looking up the knowledge graph"),
    "count_documents": ("count", "Counting matching documents"),
    "aggregate_transactions": ("aggregate", "Adding up transactions"),
    "corpus_stats": ("stats", "Reading corpus statistics"),
    "list_categories": ("categories", "Listing categories"),
}


def tool_step(name: str) -> TraceStep:
    """The trace step for a tool call, with a human label per tool (fallback: ``Using <name>``)."""
    kind, label = _TOOL_TRACE.get(name, ("tool", f"Using {name}"))
    return TraceStep(kind=kind, label=label)


def step(kind: str, label: str, detail: str = "") -> TraceStep:
    return TraceStep(kind=kind, label=label, detail=detail)


def step_event(trace_step: TraceStep) -> ChatEvent:
    return ChatEvent(type="step", delta=trace_step.label, trace_step=trace_step)
