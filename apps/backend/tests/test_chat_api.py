import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import RagAnswerer
from doktok_contracts.schemas import Citation, RagAnswer
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


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


class _SemanticChat:
    """Classifies nothing as aggregation, so chat deterministically falls through to RAG without
    touching a real model (some questions, e.g. 'what is the total?', trip the keyword gate)."""

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return '{"is_aggregation": false}'


def _client(answerer: FakeRagAnswerer) -> TestClient:
    from doktok_contracts.ports import ChatModelProvider

    registry = build_registry()
    registry.register(RagAnswerer, answerer)  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, _SemanticChat())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_requires_token() -> None:
    resp = _client(FakeRagAnswerer()).post("/api/v1/chat", json={"question": "hi"})
    assert resp.status_code == 401


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
    from doktok_contracts.ports import ChatModelProvider, DocumentRepository, RecordRepository
    from doktok_core.documents.inmemory import InMemoryDocumentRepository

    answerer = FakeRagAnswerer()
    registry = build_registry()
    registry.register(RagAnswerer, answerer)  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, FakeChatModel())  # type: ignore[type-abstract]
    registry.register(RecordRepository, FakeRecordRepository())
    registry.register(DocumentRepository, InMemoryDocumentRepository())  # type: ignore[type-abstract]
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
