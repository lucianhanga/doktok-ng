"""In-memory chat-thread repository for tests and local/dev runs (tenant-scoped, M6.4 #248)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import ChatMessage, ChatThread, Citation, RankedChunk, TurnMetrics


class InMemoryChatThreadRepository:
    def __init__(self) -> None:
        self._threads: dict[tuple[str, str], ChatThread] = {}
        self._messages: dict[tuple[str, str], list[ChatMessage]] = {}

    def create_thread(self, tenant_id: str, title: str = "") -> ChatThread:
        now = datetime.now(UTC)
        thread = ChatThread(id=uuid.uuid4().hex, title=title, created_at=now, updated_at=now)
        self._threads[(tenant_id, thread.id)] = thread
        self._messages[(tenant_id, thread.id)] = []
        return thread

    def list_threads(self, tenant_id: str, *, limit: int = 50) -> list[ChatThread]:
        threads = []
        for (tid, _), t in self._threads.items():
            if tid != tenant_id:
                continue
            msgs = self._messages.get((tid, t.id), [])
            tokens = sum(m.metrics.total_tokens for m in msgs if m.metrics is not None)
            ms = sum(m.metrics.total_ms for m in msgs if m.metrics is not None)
            threads.append(
                t.model_copy(
                    update={
                        "message_count": len(msgs),
                        "total_tokens": tokens,
                        "total_inference_ms": ms,
                    }
                )
            )
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        return threads[:limit]

    def get_messages(self, tenant_id: str, thread_id: str) -> list[ChatMessage]:
        return list(self._messages.get((tenant_id, thread_id), []))

    def append_message(
        self,
        tenant_id: str,
        thread_id: str,
        role: str,
        content: str,
        *,
        reasoning: str = "",
        citations: list[Citation] | None = None,
        ranking: list[RankedChunk] | None = None,
        metrics: TurnMetrics | None = None,
    ) -> ChatMessage:
        message = ChatMessage(
            id=uuid.uuid4().hex,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
            reasoning=reasoning,
            citations=list(citations or []),
            ranking=list(ranking or []),
            metrics=metrics,
        )
        self._messages.setdefault((tenant_id, thread_id), []).append(message)
        thread = self._threads.get((tenant_id, thread_id))
        if thread is not None:
            # Auto-seed the title from the first message only while still auto (never overwrite a
            # manual rename).
            title = thread.title
            if thread.title_source == "auto" and not title:
                title = content[:80]
            self._threads[(tenant_id, thread_id)] = thread.model_copy(
                update={"updated_at": message.created_at, "title": title}
            )
        return message

    def thread_exists(self, tenant_id: str, thread_id: str) -> bool:
        return (tenant_id, thread_id) in self._threads

    def delete_thread(self, tenant_id: str, thread_id: str) -> None:
        self._threads.pop((tenant_id, thread_id), None)
        self._messages.pop((tenant_id, thread_id), None)

    def delete_messages_from(self, tenant_id: str, thread_id: str, message_id: str) -> int:
        msgs = self._messages.get((tenant_id, thread_id))
        if not msgs:
            return 0
        idx = next((i for i, m in enumerate(msgs) if m.id == message_id), None)
        if idx is None:
            return 0
        removed = len(msgs) - idx
        self._messages[(tenant_id, thread_id)] = msgs[:idx]
        return removed

    def update_title(self, tenant_id: str, thread_id: str, title: str) -> ChatThread | None:
        thread = self._threads.get((tenant_id, thread_id))
        if thread is None:
            return None
        updated = thread.model_copy(update={"title": title, "title_source": "manual"})
        self._threads[(tenant_id, thread_id)] = updated
        return updated.model_copy(
            update={"message_count": len(self._messages.get((tenant_id, thread_id), []))}
        )
