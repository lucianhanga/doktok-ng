"""LLM-as-reranker (M6.1): one listwise call reorders the retrieved candidates by relevance.

Retrieve wide (e.g. 40), rerank, keep the best top_k. A single chat call (listwise) keeps the
latency cost to one extra request per query. Any failure/unparseable response falls back to the
original retrieval order, so the reranker can only improve, never break, retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Generator

from doktok_contracts.media import ChatChunk
from doktok_contracts.ports import ChatModelProvider, StreamingChatModelProvider
from doktok_contracts.schemas import SearchHit

logger = logging.getLogger("doktok.rag.rerank")

_MAX_PASSAGE_CHARS = 1200

_PROMPT = """You are reranking passages for a search query. Read the query and the numbered
passages, then return ONLY a JSON array of the passage numbers ordered from MOST to LEAST relevant
to the query (most relevant first), including only the {top_k} most relevant. Example: [3, 0, 5]

Query: {query}

Passages:
{passages}

JSON array:"""


def _parse_order(response: str, count: int) -> list[int]:
    match = re.search(r"\[[\d,\s]*\]", response)
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    seen: set[int] = set()
    order: list[int] = []
    for value in raw:
        if isinstance(value, int) and 0 <= value < count and value not in seen:
            order.append(value)
            seen.add(value)
    return order


class LlmReranker:
    """``Reranker`` using the chat model to listwise-rank the candidates."""

    def __init__(self, chat_model: ChatModelProvider) -> None:
        self._chat = chat_model

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        if len(hits) <= 1:
            return hits[:top_k]
        passages = "\n".join(
            f"[{i}] {(hit.text or hit.snippet)[:_MAX_PASSAGE_CHARS]}" for i, hit in enumerate(hits)
        )
        prompt = _PROMPT.format(top_k=top_k, query=query[:500], passages=passages)
        try:
            order = _parse_order(self._chat.complete(prompt), len(hits))
        except Exception:  # noqa: BLE001 - a rerank failure falls back to retrieval order
            logger.warning("LLM reranker failed; using retrieval order", exc_info=True)
            order = []
        if not order:
            return hits[:top_k]
        return [hits[i] for i in order][:top_k]

    def rerank_stream(
        self, query: str, hits: list[SearchHit], *, top_k: int, think: bool | None = None
    ) -> Generator[ChatChunk, None, list[SearchHit]]:
        """Like ``rerank`` but streams the model's reasoning chunks (M8): yields ChatChunk(kind=
        'reasoning') as the model thinks, then RETURNS the reordered hits (via StopIteration.value).
        Falls back to retrieval order on any failure or a non-streaming model."""
        if len(hits) <= 1:
            return hits[:top_k]
        passages = "\n".join(
            f"[{i}] {(hit.text or hit.snippet)[:_MAX_PASSAGE_CHARS]}" for i, hit in enumerate(hits)
        )
        prompt = _PROMPT.format(top_k=top_k, query=query[:500], passages=passages)
        parts: list[str] = []
        try:
            if isinstance(self._chat, StreamingChatModelProvider):
                for chunk in self._chat.stream_complete(prompt, think=think):
                    if chunk.kind == "reasoning":
                        yield chunk
                    else:
                        parts.append(chunk.text)
            else:
                parts.append(self._chat.complete(prompt))
        except Exception:  # noqa: BLE001 - a rerank failure falls back to retrieval order
            logger.warning("LLM reranker (stream) failed; using retrieval order", exc_info=True)
            return hits[:top_k]
        order = _parse_order("".join(parts), len(hits))
        if not order:
            return hits[:top_k]
        return [hits[i] for i in order][:top_k]
