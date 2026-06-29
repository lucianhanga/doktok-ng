"""Rolling conversation-summary STM (ADR-0022 Phase 3): folding + compaction + watermark."""

from __future__ import annotations

from typing import cast

from doktok_contracts.ports import ChatModelProvider, ChatThreadRepository
from doktok_contracts.schemas import ChatTurn
from doktok_core.chat.inmemory import InMemoryChatThreadRepository
from doktok_core.chat.summary import compact_history, fold_summary, prepare_context


class _Model:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "FOLDED SUMMARY"


def _turns(n: int) -> list[ChatTurn]:
    out: list[ChatTurn] = []
    for i in range(n):
        out.append(ChatTurn(role="user", content=f"q{i}"))
        out.append(ChatTurn(role="assistant", content=f"a{i}"))
    return out


def _model() -> ChatModelProvider:
    return cast(ChatModelProvider, _Model())


def test_fold_summary_noop_without_overflow() -> None:
    assert fold_summary("prev", [], _model()) == "prev"


def test_fold_summary_calls_model() -> None:
    out = fold_summary("prev", [ChatTurn(role="user", content="hi")], _model())
    assert out == "FOLDED SUMMARY"


def test_compact_history_prepends_summary_and_keeps_recent() -> None:
    turns = _turns(10)  # 20 turns
    compact = compact_history(turns, summary="S", keep_recent=4)
    assert compact[0].role == "system" and "S" in compact[0].content
    assert [t.content for t in compact[1:]] == [t.content for t in turns[-4:]]


def test_compact_history_no_summary_returns_recent_only() -> None:
    turns = _turns(10)
    compact = compact_history(turns, summary="", keep_recent=4)
    assert all(t.role != "system" for t in compact) and len(compact) == 4


def test_prepare_context_noop_for_short_thread() -> None:
    repo = cast(ChatThreadRepository, InMemoryChatThreadRepository())
    history = _turns(2)  # 4 turns <= keep_recent
    out = prepare_context(repo, _model(), "t", "th1", history, keep_recent=8)
    assert out == history  # unchanged


def test_prepare_context_noop_when_stateless() -> None:
    repo = cast(ChatThreadRepository, InMemoryChatThreadRepository())
    history = _turns(20)
    out = prepare_context(repo, _model(), "t", None, history, keep_recent=8)
    assert out == history  # no thread => no compaction


def test_prepare_context_folds_overflow_and_persists_watermark() -> None:
    repo = InMemoryChatThreadRepository()
    history = _turns(10)  # 20 turns, keep_recent=4 -> 16 overflow
    out = prepare_context(
        cast(ChatThreadRepository, repo), _model(), "t", "th1", history, keep_recent=4
    )
    # compacted: summary system turn + last 4 turns
    assert out[0].role == "system" and "FOLDED SUMMARY" in out[0].content
    assert len(out) == 5
    summary, through = repo.get_summary("t", "th1")
    assert summary == "FOLDED SUMMARY" and through == len(history) - 4


def test_prepare_context_only_folds_new_overflow() -> None:
    repo = InMemoryChatThreadRepository()
    repo.update_summary("t", "th1", "OLD", 12)  # already folded 12 messages
    model = _Model()
    history = _turns(10)  # 20 turns
    prepare_context(
        cast(ChatThreadRepository, repo),
        cast(ChatModelProvider, model),
        "t",
        "th1",
        history,
        keep_recent=4,
    )
    # only messages 12..16 (the new overflow) are folded, not all 16
    assert "q6" in model.prompts[0] and "q0" not in model.prompts[0]
