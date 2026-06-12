import os
from datetime import date

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import RecordRepository
from doktok_contracts.schemas import ExtractedRecord
from doktok_core.aggregation.inmemory import InMemoryRecordRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(repo: RecordRepository) -> TestClient:
    registry = build_registry()
    registry.register(RecordRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _repo() -> InMemoryRecordRepository:
    repo = InMemoryRecordRepository()
    repo.replace_for_document(
        "tenant-a",
        "d1",
        [
            ExtractedRecord(
                id="r1",
                tenant_id="tenant-a",
                document_id="d1",
                raw_text="x",
                occurred_on=date(2024, 2, 3),
                amount_minor=4250,
                currency="EUR",
                direction="debit",
                merchant_normalized="block house hamburg",
            ),
        ],
    )
    return repo


def test_requires_token() -> None:
    assert _client(_repo()).post("/api/v1/aggregate", json={}).status_code == 401


def test_aggregates_for_caller_tenant() -> None:
    resp = _client(_repo()).post(
        "/api/v1/aggregate",
        json={"operation": "sum", "merchant": "block house"},
        headers={"Authorization": "Bearer tok-a"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["by_currency"][0] == {"currency": "EUR", "total_minor": 4250, "count": 1}
    assert body["samples"][0]["id"] == "r1"
