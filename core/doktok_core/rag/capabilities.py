"""Deterministic chat capabilities (M6.5): a tiny, default-deny set of facts the conversational RAG
answers directly, in code, without the local model deciding to invoke a tool.

ADR-0018 rejected an agent tool-calling loop (local-model tool-calling is unreliable). Instead, a
high-confidence keyword match short-circuits the pipeline: the backend computes a verified fact and
returns it as the answer; anything that does not clearly match falls through to the grounded-RAG
path unchanged. A uniform registry keeps adding a couple more simple capabilities a config change.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

# Keep the matcher tight (default-deny): only short, self-contained "what is the time/date" style
# questions, so document questions like "what time did the meeting start" fall through to RAG.
_MAX_WORDS = 12

_TIME_PATTERN = re.compile(
    r"""\b(
        what(?:'?s|\s+is)?\s+the\s+(?:current\s+)?(?:time|date)   # what's the time / the date
        | what\s+time\s+is\s+it                                   # what time is it
        | what\s+(?:day|date)\s+is\s+(?:it|today)                 # what day/date is it/today
        | (?:the\s+)?current\s+(?:date|time)                      # current time / the current date
        | today'?s\s+date                                        # today's date
        | what\s+is\s+today'?s\s+date
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def now_local() -> datetime:
    """Current local, timezone-aware time. Indirected so tests can patch it."""
    return datetime.now().astimezone()


def current_time_answer(now: datetime) -> str:
    offset = now.strftime("%z")  # e.g. +0200
    utc = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"It is {now:%H:%M} on {now:%A}, {now.day} {now:%B %Y} ({utc})."


@dataclass(frozen=True)
class Capability:
    """A deterministic answer the backend computes directly (no retrieval, no model invocation)."""

    name: str
    pattern: re.Pattern[str]
    answer: Callable[[datetime], str]


CAPABILITIES: list[Capability] = [
    Capability(name="current_time", pattern=_TIME_PATTERN, answer=current_time_answer),
]


def match_capability(question: str) -> Capability | None:
    """The capability a short, high-confidence question triggers, or None to fall through to RAG."""
    question = question.strip()
    if not question or len(question.split()) > _MAX_WORDS:
        return None
    for capability in CAPABILITIES:
        if capability.pattern.search(question):
            return capability
    return None
