"""Deterministic chat capabilities (M6.5): the default-deny matcher + current-time answer."""

from __future__ import annotations

from datetime import datetime

from doktok_core.rag.capabilities import current_time_answer, match_capability


def test_matches_short_time_questions() -> None:
    for q in [
        "what time is it",
        "What's the time?",
        "what is the current date",
        "what day is today?",
        "today's date",
        "current time",
        "what is the current time please",
    ]:
        assert match_capability(q) is not None, q


def test_ignores_document_and_long_questions() -> None:
    for q in [
        "what time did the meeting start according to the report",
        "summarize the latest invoice for me",
        "what is the total due on invoice INV-2026-001",
        "when is the contract renewal date in the document",
        "",
    ]:
        assert match_capability(q) is None, q


def test_current_time_answer_format() -> None:
    now = datetime(2026, 6, 14, 14, 32).astimezone()
    text = current_time_answer(now)
    assert "14:32" in text and "June 2026" in text and "14" in text
