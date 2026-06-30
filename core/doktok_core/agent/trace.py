"""Reusable chronological activity-trace steps for chat (ADR-0022).

A single mechanism emitted by BOTH the single-agent loop and the multi-agent graph, so the UI
renders one consistent trace ("understanding → searching → knowledge graph → composing"). Each step
is a typed ``TraceStep`` (kind + human label); ``step_event`` wraps it as a ``ChatEvent`` (carrying
the label in ``delta`` too, for any legacy delta-only consumer).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from doktok_contracts.schemas import ChatEvent, TraceStep


def _now() -> str:
    """ISO-8601 UTC timestamp for a trace step (the moment it is emitted)."""
    return datetime.now(UTC).isoformat()


# Tool name -> (kind, human label). Kind drives the per-step icon/colour in the UI.
_TOOL_TRACE: dict[str, tuple[str, str]] = {
    "retrieve_passages": ("retrieve", "Searching your documents"),
    "graph_lookup": ("graph", "Looking up the knowledge graph"),
    "count_documents": ("count", "Counting matching documents"),
    "aggregate_transactions": ("aggregate", "Adding up transactions"),
    "corpus_stats": ("stats", "Reading corpus statistics"),
    "list_categories": ("categories", "Listing categories"),
}


def _format_args(args: Mapping[str, object]) -> str:
    """Compact one-line summary of tool arguments for the activity trace (values truncated)."""
    parts: list[str] = []
    for key, value in args.items():
        text = str(value).strip()
        if not text:
            continue
        if len(text) > 40:
            text = text[:39] + "…"
        parts.append(f"{key}: {text}")
    return " · ".join(parts)


def tool_step(name: str, args: Mapping[str, object] | None = None) -> TraceStep:
    """The trace step for a tool call, with a human label per tool (fallback: ``Using <name>``).

    When ``args`` are given, a compact summary is put in ``detail`` so the activity timeline shows
    what each tool was called with (ToolIO parity with personalAI).
    """
    kind, label = _TOOL_TRACE.get(name, ("tool", f"Using {name}"))
    return TraceStep(kind=kind, label=label, detail=_format_args(args) if args else "", at=_now())


def step(kind: str, label: str, detail: str = "") -> TraceStep:
    return TraceStep(kind=kind, label=label, detail=detail, at=_now())


def step_event(trace_step: TraceStep) -> ChatEvent:
    return ChatEvent(type="step", delta=trace_step.label, trace_step=trace_step)
