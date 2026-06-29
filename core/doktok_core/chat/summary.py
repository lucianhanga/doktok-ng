"""Rolling conversation-summary short-term memory (ADR-0022 Phase 3).

A long thread would eventually overflow the model context. Instead of dropping old turns, we keep
the most recent ``keep_recent`` verbatim and fold everything older into a running summary stored on
the thread (``summary`` + ``summary_through`` watermark, migration 0036). Each turn only summarizes
the *new* overflow, so the cost is bounded. Best-effort: a summarization failure leaves the prior
summary in place and never breaks chat. Mirrors personalAI's ``_assemble_stm``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from doktok_contracts.ports import ChatModelProvider, ChatThreadRepository
from doktok_contracts.schemas import ChatTurn

logger = logging.getLogger("doktok.chat.summary")

KEEP_RECENT = 8

_FOLD_PROMPT = (
    "You maintain a running summary of a conversation between a user and an assistant about the "
    "user's documents. Update the summary to incorporate the new turns below, preserving facts, "
    "named entities, document references and any unresolved questions. Be concise (<= 200 words). "
    "Reply with ONLY the updated summary.\n\n"
    "Current summary:\n{summary}\n\nNew turns:\n{turns}\n\nUpdated summary:"
)


def fold_summary(existing: str, overflow: Sequence[ChatTurn], model: ChatModelProvider) -> str:
    """Fold ``overflow`` turns into ``existing``, returning the updated summary (or ``existing`` on
    no overflow / any failure - summarization never breaks the turn)."""
    if not overflow:
        return existing
    turns_text = "\n".join(f"{t.role}: {t.content}" for t in overflow)
    prompt = _FOLD_PROMPT.format(summary=existing or "(none)", turns=turns_text)
    try:
        return model.complete(prompt).strip() or existing
    except Exception:  # noqa: BLE001 - best-effort STM; keep the prior summary
        logger.debug("summary fold failed; keeping prior summary", exc_info=True)
        return existing


def compact_history(
    full_history: Sequence[ChatTurn], *, summary: str, keep_recent: int = KEEP_RECENT
) -> list[ChatTurn]:
    """The context turns: a synthetic system turn carrying ``summary`` (if any) + the most recent
    ``keep_recent`` turns. The summary is truncated to fit the ChatTurn content bound."""
    recent = list(full_history[-keep_recent:]) if keep_recent else list(full_history)
    if not summary:
        return recent
    head = ChatTurn(role="system", content=f"Summary of earlier conversation:\n{summary}"[:8000])
    return [head, *recent]


def prepare_context(
    repo: ChatThreadRepository,
    model: ChatModelProvider,
    tenant_id: str,
    thread_id: str | None,
    full_history: Sequence[ChatTurn],
    *,
    keep_recent: int = KEEP_RECENT,
) -> list[ChatTurn]:
    """Fold overflow beyond ``keep_recent`` into the thread's rolling summary (persisted), then
    return the compacted context. A no-op (returns ``full_history`` unchanged) for stateless chat,
    or a thread within ``keep_recent`` turns - short conversations behave exactly as before."""
    history = list(full_history)
    if thread_id is None or len(history) <= keep_recent:
        return history
    summary, through = repo.get_summary(tenant_id, thread_id)
    cutoff = len(history) - keep_recent
    overflow = history[through:cutoff]
    if overflow:
        summary = fold_summary(summary, overflow, model)
        repo.update_summary(tenant_id, thread_id, summary, cutoff)
    return compact_history(history, summary=summary, keep_recent=keep_recent)
