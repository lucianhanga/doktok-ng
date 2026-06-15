"""In-memory chat-thread repository for tests and local/dev runs (tenant-scoped, M6.4 #248)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doktok_contracts.schemas import ChatMessage, ChatThread, Citation


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
        threads = [
            t.model_copy(update={"message_count": len(self._messages.get((tenant_id, t.id), []))})
            for (tid, _), t in self._threads.items()
            if tid == tenant_id
        ]
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
    ) -> ChatMessage:
        message = ChatMessage(
            id=uuid.uuid4().hex,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
            reasoning=reasoning,
            citations=list(citations or []),
        )
        self._messages.setdefault((tenant_id, thread_id), []).append(message)
        thread = self._threads.get((tenant_id, thread_id))
        if thread is not None:
            title = thread.title or content[:80]
            self._threads[(tenant_id, thread_id)] = thread.model_copy(
                update={"updated_at": message.created_at, "title": title}
            )
        return message

    def thread_exists(self, tenant_id: str, thread_id: str) -> bool:
        return (tenant_id, thread_id) in self._threads

    def delete_thread(self, tenant_id: str, thread_id: str) -> None:
        self._threads.pop((tenant_id, thread_id), None)
        self._messages.pop((tenant_id, thread_id), None)
