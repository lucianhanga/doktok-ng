import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import ChatEvent, Citation, RagAnswer
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeRagAnswerer:
    def __init__(self) -> None:
        self.seen: tuple[str, str, int] | None = None
        self.seen_history: list[object] | None = None

    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer:
        self.seen = (tenant_id, question, limit)
        return RagAnswer(
            answer="The total is 42 [1].",
            citations=[Citation(index=1, document_id="d1", chunk_id="c1", snippet="...42...")],
            grounded=True,
        )

    def answer_thread(self, tenant_id, history, question, limit=8):  # type: ignore[no-untyped-def]
        self.seen_history = list(history)
        return self.answer(tenant_id, question, limit)

    def answer_thread_stream(self, tenant_id, history, question, limit=8, *, reasoning=None):  # type: ignore[no-untyped-def]
        self.seen_history = list(history)
        ans = self.answer(tenant_id, question, limit)
        yield ChatEvent(type="meta")
        yield ChatEvent(type="token", delta=ans.answer)
        yield ChatEvent(type="sources", citations=ans.citations)
        yield ChatEvent(type="done", grounded=ans.grounded)


class _SemanticChat:
    """Classifies nothing as aggregation, so chat deterministically falls through to RAG without
    touching a real model (some questions, e.g. 'what is the total?', trip the keyword gate)."""

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return '{"is_aggregation": false}'


def _client(answerer: FakeRagAnswerer) -> TestClient:
    from doktok_contracts.ports import ChatModelProvider, ChatThreadRepository
    from doktok_core.chat.inmemory import InMemoryChatThreadRepository

    registry = build_registry()
    registry.register(RagAnswerer, answerer)  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, _SemanticChat())  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, InMemoryChatThreadRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_requires_token() -> None:
    resp = _client(FakeRagAnswerer()).post("/api/v1/chat", json={"question": "hi"})
    assert resp.status_code == 401


def test_retrieve_endpoint_is_read_only_and_echoes_query() -> None:
    # The Retrieval Explorer never errors the UI: with no tool repos wired it returns the query and
    # an empty evidence list (best-effort), proving the route + response shape (ADR-0022).
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat/retrieve",
        json={"question": "what is the rent?"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "what is the rent?"
    assert body["citations"] == []


def test_agent_mode_falls_back_to_classic_when_model_lacks_tool_calling() -> None:
    # The configured model (_SemanticChat) can't tool-call, so agent_mode="agent" must degrade to
    # the classic RAG path rather than erroring (ADR-0022: the agent path never breaks chat).
    answerer = FakeRagAnswerer()
    resp = _client(answerer).post(
        "/api/v1/chat",
        json={"question": "what is the total?", "agent_mode": "agent"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "The total is 42 [1]."
    assert answerer.seen is not None  # the classic answerer was used


def test_thread_persists_turns_and_loads_history() -> None:
    answerer = FakeRagAnswerer()
    client = _client(answerer)
    h = {"Authorization": "Bearer tok-a"}

    thread_id = client.post("/api/v1/chat/threads", headers=h).json()["id"]
    first = client.post(
        "/api/v1/chat", json={"question": "what is the total?", "thread_id": thread_id}, headers=h
    )
    assert first.status_code == 200

    # Both the user question and the assistant answer were persisted to the thread.
    messages = client.get(f"/api/v1/chat/threads/{thread_id}/messages", headers=h).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "what is the total?"
    assert messages[1]["content"] == "The total is 42 [1]."

    # A follow-up loads the prior history server-side (not from the request body).
    client.post(
        "/api/v1/chat", json={"question": "and in March?", "thread_id": thread_id}, headers=h
    )
    assert answerer.seen_history is not None and len(answerer.seen_history) == 2

    # The thread is listed with its title seeded from the first message.
    threads = client.get("/api/v1/chat/threads", headers=h).json()
    assert threads[0]["id"] == thread_id and threads[0]["title"] == "what is the total?"


def test_chat_with_unknown_thread_is_404() -> None:
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat",
        json={"question": "hi", "thread_id": "does-not-exist"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 404


def test_rename_thread_sets_manual_title_and_stops_autoseed() -> None:
    client = _client(FakeRagAnswerer())
    h = {"Authorization": "Bearer tok-a"}
    tid = client.post("/api/v1/chat/threads", headers=h).json()["id"]

    renamed = client.patch(f"/api/v1/chat/threads/{tid}", json={"title": "Tax stuff"}, headers=h)
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["title"] == "Tax stuff"
    assert body["title_source"] == "manual"

    # A subsequent message must NOT overwrite the manual title with the auto-seed.
    client.post(
        "/api/v1/chat", json={"question": "first question here", "thread_id": tid}, headers=h
    )
    threads = client.get("/api/v1/chat/threads", headers=h).json()
    assert threads[0]["title"] == "Tax stuff"


def test_truncate_deletes_a_message_and_everything_after() -> None:
    client = _client(FakeRagAnswerer())
    h = {"Authorization": "Bearer tok-a"}
    tid = client.post("/api/v1/chat/threads", headers=h).json()["id"]
    client.post("/api/v1/chat", json={"question": "first", "thread_id": tid}, headers=h)
    client.post("/api/v1/chat", json={"question": "second", "thread_id": tid}, headers=h)

    msgs = client.get(f"/api/v1/chat/threads/{tid}/messages", headers=h).json()
    assert [m["content"] for m in msgs] == [
        "first",
        "The total is 42 [1].",
        "second",
        "The total is 42 [1].",
    ]
    second_user = msgs[2]  # the "second" user turn

    # Truncate from the second question -> it and its answer are removed; the first turn remains.
    resp = client.delete(
        f"/api/v1/chat/threads/{tid}/messages/{second_user['id']}/after", headers=h
    )
    assert resp.status_code == 204
    after = client.get(f"/api/v1/chat/threads/{tid}/messages", headers=h).json()
    assert [m["content"] for m in after] == ["first", "The total is 42 [1]."]


def test_rename_validation_and_tenant_isolation() -> None:
    client = _client(FakeRagAnswerer())
    a = {"Authorization": "Bearer tok-a"}
    tid = client.post("/api/v1/chat/threads", headers=a).json()["id"]
    # Blank title -> 422.
    assert (
        client.patch(f"/api/v1/chat/threads/{tid}", json={"title": "   "}, headers=a).status_code
        == 422
    )
    # Another tenant cannot rename it -> 404 (no existence leak).
    cross = client.patch(
        f"/api/v1/chat/threads/{tid}",
        json={"title": "hijack"},
        headers={"Authorization": "Bearer tok-b"},
    )
    assert cross.status_code == 404


def test_threads_are_tenant_isolated() -> None:
    client = _client(FakeRagAnswerer())
    a_thread = client.post(
        "/api/v1/chat/threads", headers={"Authorization": "Bearer tok-a"}
    ).json()["id"]
    # Tenant-b cannot see or read tenant-a's thread.
    b_headers = {"Authorization": "Bearer tok-b"}
    assert client.get("/api/v1/chat/threads", headers=b_headers).json() == []
    cross = client.get(f"/api/v1/chat/threads/{a_thread}/messages", headers=b_headers)
    assert cross.status_code == 404


def test_returns_grounded_answer_for_caller_tenant() -> None:
    answerer = FakeRagAnswerer()
    resp = _client(answerer).post(
        "/api/v1/chat",
        json={"question": "what is the total?", "limit": 5},
        headers={"Authorization": "Bearer tok-a"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["grounded"] is True
    assert body["answer"] == "The total is 42 [1]."
    assert body["citations"][0]["document_id"] == "d1"
    assert answerer.seen == ("tenant-a", "what is the total?", 5)


def test_passes_conversation_history_for_followups() -> None:
    answerer = FakeRagAnswerer()
    resp = _client(answerer).post(
        "/api/v1/chat",
        json={
            "question": "what about March?",
            "history": [
                {"role": "user", "content": "how much at Block House?"},
                {"role": "assistant", "content": "EUR 120 [1]."},
            ],
        },
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 200
    assert answerer.seen_history is not None and len(answerer.seen_history) == 2


def test_stream_endpoint_emits_sse_events() -> None:
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat/stream",
        json={"question": "what is the total?"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "event: token" in body and "The total is 42" in body
    assert "event: sources" in body and "event: done" in body


def test_rejects_empty_question() -> None:
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat", json={"question": ""}, headers={"Authorization": "Bearer tok-a"}
    )
    assert resp.status_code == 422  # min_length=1


def test_rejects_oversized_question() -> None:
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat",
        json={"question": "x" * 5000},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 422  # max_length=4000


def test_response_carries_request_id_header() -> None:
    resp = _client(FakeRagAnswerer()).post(
        "/api/v1/chat",
        json={"question": "hi"},
        headers={"Authorization": "Bearer tok-a", "X-Request-ID": "abc123"},
    )
    assert resp.headers.get("X-Request-ID") == "abc123"


# --- M6.3 #158: an aggregation question is answered deterministically from records, not RAG ---


class FakeChatModel:
    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return (
            '{"is_aggregation": true, "operation": "sum", "merchant": "Block House", '
            '"direction": "debit", "currency": "EUR", "date_from": null, "date_to": null}'
        )


class FakeRecordRepository:
    def aggregate(self, tenant_id: str, intent: object) -> object:  # noqa: ARG002
        from doktok_contracts.schemas import AggregationBucket, AggregationResult, ExtractedRecord

        return AggregationResult(
            operation="sum",
            count=2,
            by_currency=[AggregationBucket(currency="EUR", total_minor=8500, count=2)],
            samples=[
                ExtractedRecord(
                    id="r1",
                    tenant_id=tenant_id,
                    document_id="d1",
                    raw_text="BLOCK HOUSE 42.50",
                    amount_minor=4250,
                    currency="EUR",
                    direction="debit",
                )
            ],
        )

    def replace_for_document(self, tenant_id: str, document_id: str, records: object) -> None: ...  # noqa: ARG002


def test_aggregation_question_answered_from_records_not_rag() -> None:
    from doktok_contracts.ports import (
        ChatModelProvider,
        ChatThreadRepository,
        DocumentRepository,
        RecordRepository,
    )
    from doktok_core.chat.inmemory import InMemoryChatThreadRepository
    from doktok_core.documents.inmemory import InMemoryDocumentRepository

    answerer = FakeRagAnswerer()
    registry = build_registry()
    registry.register(RagAnswerer, answerer)  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, FakeChatModel())  # type: ignore[type-abstract]
    registry.register(RecordRepository, FakeRecordRepository())
    registry.register(DocumentRepository, InMemoryDocumentRepository())  # type: ignore[type-abstract]
    registry.register(ChatThreadRepository, InMemoryChatThreadRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))

    resp = client.post(
        "/api/v1/chat",
        json={"question": "how much did I spend at Block House?"},
        headers={"Authorization": "Bearer tok-a"},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert "85.00" in body["answer"] and "EUR" in body["answer"]  # deterministic total
    assert body["citations"][0]["document_id"] == "d1"
    assert answerer.seen is None  # RAG was bypassed
