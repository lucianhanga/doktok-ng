"""Chat capacity + SSE disconnect handling (#626, security audit F-14).

A global semaphore bounds concurrent answer generation (the model fleet is small); when it is
full the caller gets 429 + Retry-After instead of queueing behind zombie work. The SSE wrapper
stops streaming and closes the generator (aborting the provider connection) once the client goes
away, so abandoned streams stop burning the model.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_api.routers.chat import stream_with_disconnect_guard
from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import ChatEvent, Citation, RagAnswer
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer tok-a"}


class FakeRagAnswerer:
    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer:
        return RagAnswer(
            answer="The total is 42 [1].",
            citations=[Citation(index=1, document_id="d1", chunk_id="c1", snippet="...42...")],
            grounded=True,
        )

    def answer_thread(self, tenant_id, history, question, limit=8):  # type: ignore[no-untyped-def]
        return self.answer(tenant_id, question, limit)

    def answer_thread_stream(self, tenant_id, history, question, limit=8, *, reasoning=None):  # type: ignore[no-untyped-def]
        ans = self.answer(tenant_id, question, limit)
        yield ChatEvent(type="meta")
        yield ChatEvent(type="token", delta=ans.answer)
        yield ChatEvent(type="sources", citations=ans.citations)
        yield ChatEvent(type="done", grounded=ans.grounded)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _app(tmp_path: Path, *, max_concurrent: int = 1):  # type: ignore[no-untyped-def]
    from doktok_contracts.ports import ChatModelProvider, ChatThreadRepository
    from doktok_core.chat.inmemory import InMemoryChatThreadRepository

    registry = build_registry()
    registry.register(RagAnswerer, FakeRagAnswerer())  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, _SemanticChat())  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, InMemoryChatThreadRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-a": "tenant-a"},
        files_root=str(tmp_path),
        chat_max_concurrent=max_concurrent,
        _env_file=None,
    )
    return create_app(settings=settings, registry=registry)


class _SemanticChat:
    """Nothing classifies as aggregation, so chat falls through to RAG without a real model/DB."""

    def complete(self, prompt: str) -> str:
        return '{"is_aggregation": false}'


def test_chat_slot_full_returns_429_with_retry_after(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    # Fill the only slot; the next generation must be refused, not queued.
    assert app.state.chat_semaphore.acquire(blocking=False)
    for path in ("/api/v1/chat", "/api/v1/chat/stream"):
        resp = client.post(path, json={"question": "hi"}, headers=AUTH)
        assert resp.status_code == 429, path
        assert int(resp.headers["Retry-After"]) >= 1


def test_chat_slot_releases_after_the_answer(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)
    resp = client.post("/api/v1/chat", json={"question": "hi"}, headers=AUTH)
    assert resp.status_code == 200
    # The slot is free again for the next caller.
    assert app.state.chat_semaphore.acquire(blocking=False)


def test_disconnect_guard_stops_the_stream_and_closes_the_generator() -> None:
    closed = False

    def _gen() -> Iterator[str]:
        nonlocal closed
        try:
            yield "a"
            yield "b"
            yield "c"
        finally:
            closed = True

    class _FakeRequest:
        calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 1  # the client goes away after the first event

    async def _collect() -> list[str]:
        return [chunk async for chunk in stream_with_disconnect_guard(_gen(), _FakeRequest())]  # type: ignore[arg-type]

    chunks = asyncio.run(_collect())
    assert chunks == ["a"]  # the remaining events were never pulled
    assert closed is True  # the generator's finally ran (provider connection aborted)
