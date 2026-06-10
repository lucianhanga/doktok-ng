import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import StatsRepository
from doktok_contracts.schemas import StatsSummary
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeStatsRepository:
    def summary(self, tenant_id: str) -> StatsSummary:  # noqa: ARG002
        return StatsSummary(documents=3, jobs={"active": 2, "failed": 1}, entities=5)


def _client() -> TestClient:
    registry = build_registry()
    registry.register(StatsRepository, FakeStatsRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_requires_token() -> None:
    assert _client().get("/api/v1/stats").status_code == 401


def test_returns_summary() -> None:
    body = _client().get("/api/v1/stats", headers={"Authorization": "Bearer tok-a"}).json()
    assert body == {"documents": 3, "jobs": {"active": 2, "failed": 1}, "entities": 5}
