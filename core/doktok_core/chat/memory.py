"""Long-term semantic memory for chat (ADR-0022): recall relevant facts from past conversations
and write a memory after a turn. Opt-in per turn (default off = private / "incognito"); both
recall and write are best-effort and never break the turn. Cross-thread, tenant-scoped.
"""

from __future__ import annotations

import logging
import uuid

from doktok_contracts.ports import EmbeddingProvider, MemoryRepository
from doktok_contracts.schemas import ChatTurn, Memory

logger = logging.getLogger("doktok.chat.memory")

RECALL_LIMIT = 4
_MEMORY_CHARS = 2000


def recall_context(
    repo: MemoryRepository,
    embeddings: EmbeddingProvider,
    tenant_id: str,
    question: str,
    *,
    limit: int = RECALL_LIMIT,
) -> list[ChatTurn]:
    """The relevant long-term memories for ``question`` as a single system context turn (empty list
    when memory is unavailable or nothing is recalled - so callers can always splice it in)."""
    try:
        vector = embeddings.embed([question])[0]
        memories = repo.recall(tenant_id, vector, limit=limit)
    except Exception:  # noqa: BLE001 - recall is best-effort; never break the turn
        logger.debug("memory recall failed", exc_info=True)
        return []
    if not memories:
        return []
    body = "\n".join(f"- {m.text}" for m in memories)
    content = f"Relevant memories from earlier conversations:\n{body}"
    return [ChatTurn(role="system", content=content[:8000])]


def remember_turn(
    repo: MemoryRepository,
    embeddings: EmbeddingProvider,
    tenant_id: str,
    question: str,
    answer: str,
    *,
    thread_id: str | None = None,
) -> None:
    """Store a memory summarizing a completed turn (best-effort; a failure is swallowed)."""
    text = f"Q: {question}\nA: {answer}".strip()[:_MEMORY_CHARS]
    if not text:
        return
    source = {"thread_id": thread_id} if thread_id else {}
    try:
        vector = embeddings.embed([text])[0]
        repo.remember(
            tenant_id,
            Memory(id=uuid.uuid4().hex, kind="conversation", text=text, source=source),
            vector,
        )
    except Exception:  # noqa: BLE001 - writing memory must not fail the turn
        logger.debug("memory write failed", exc_info=True)
