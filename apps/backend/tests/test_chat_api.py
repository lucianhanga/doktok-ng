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

    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer:
        self.seen = (tenant_id, question, limit)
        return RagAnswer(
            answer="The total is 42 [1].",
            citations=[Citation(index=1, document_id="d1", chunk_id="c1", snippet="...42...")],
            grounded=True,
        )


def _client(answerer: FakeRagAnswerer) -> TestClient:
    registry = build_registry()
    registry.register(RagAnswerer, answerer)  # type: ignore[type-abstract]
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
