"""Long-term semantic memory recall + write (ADR-0022). Fakes for the embedder; no DB."""

from __future__ import annotations

from typing import cast

from doktok_contracts.ports import EmbeddingProvider, MemoryRepository
from doktok_core.chat.inmemory import InMemoryMemoryRepository
from doktok_core.chat.memory import recall_context, remember_turn


class _Embed:
    """Maps a few phrases to fixed 3-d vectors so cosine recall is deterministic."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        if "rent" in t:
            return [1.0, 0.0, 0.0]
        if "insur" in t:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class _BrokenEmbed:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("ollama down")


def _embed() -> EmbeddingProvider:
    return cast(EmbeddingProvider, _Embed())


def test_remember_then_recall_returns_relevant_memory() -> None:
    repo = InMemoryMemoryRepository()
    mrepo = cast(MemoryRepository, repo)
    remember_turn(
        mrepo, _embed(), "t", "what is my rent?", "Your rent is 900 EUR.", thread_id="th1"
    )
    remember_turn(mrepo, _embed(), "t", "who insures me?", "Allianz.", thread_id="th1")

    ctx = recall_context(mrepo, _embed(), "t", "tell me about the rent again")
    assert len(ctx) == 1 and ctx[0].role == "system"
    assert "rent is 900" in ctx[0].content.lower() or "rent is 900" in ctx[0].content
    assert "earlier conversations" in ctx[0].content.lower()


def test_recall_empty_when_no_memories() -> None:
    assert (
        recall_context(cast(MemoryRepository, InMemoryMemoryRepository()), _embed(), "t", "q") == []
    )


def test_recall_is_tenant_scoped() -> None:
    repo = InMemoryMemoryRepository()
    mrepo = cast(MemoryRepository, repo)
    remember_turn(mrepo, _embed(), "tenant-a", "rent?", "900 EUR", thread_id=None)
    assert recall_context(mrepo, _embed(), "tenant-b", "rent?") == []


def test_recall_swallows_embedding_failure() -> None:
    repo = cast(MemoryRepository, InMemoryMemoryRepository())
    assert recall_context(repo, cast(EmbeddingProvider, _BrokenEmbed()), "t", "rent?") == []


def test_remember_swallows_embedding_failure() -> None:
    repo = InMemoryMemoryRepository()
    remember_turn(
        cast(MemoryRepository, repo), cast(EmbeddingProvider, _BrokenEmbed()), "t", "q", "a"
    )
    # nothing stored, no raise
    assert repo.recall("t", [1.0, 0.0, 0.0]) == []
