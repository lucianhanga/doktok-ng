import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import IngestionJobRepository
from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_core.config import Settings
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ignore ambient DOKTOK_* (e.g. exported by `make` from .env) so settings come only from the
    # explicit values each test passes.
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _job(job_id: str, tenant: str) -> IngestionJob:
    return IngestionJob(
        id=job_id,
        tenant_id=tenant,
        source_path=f"/in.process/{job_id}/source",
        status=JobStatus.NORMALIZING,
        detected_mime="text/plain",
        sha256="a" * 64,
        started_at=datetime.now(UTC),
    )


def _client(tenant_tokens: dict[str, str], *jobs: IngestionJob) -> TestClient:
    repo = InMemoryIngestionJobRepository()
    for job in jobs:
        repo.add(job)
    registry = build_registry()
    registry.register(IngestionJobRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=tenant_tokens, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public() -> None:
    client = _client(TOKENS)
    assert client.get("/health").status_code == 200


def test_missing_token_is_401() -> None:
    client = _client(TOKENS, _job("j1", "tenant-a"))
    assert client.get("/api/v1/ingestion/jobs").status_code == 401


def test_invalid_token_is_401() -> None:
    client = _client(TOKENS, _job("j1", "tenant-a"))
    assert client.get("/api/v1/ingestion/jobs", headers=_auth("nope")).status_code == 401


def test_fail_closed_when_no_tokens_configured() -> None:
    client = _client({}, _job("j1", "tenant-a"))
    assert client.get("/api/v1/ingestion/jobs", headers=_auth("tok-a")).status_code == 503


def test_lists_only_callers_tenant() -> None:
    client = _client(TOKENS, _job("a-job", "tenant-a"), _job("b-job", "tenant-b"))
    response = client.get("/api/v1/ingestion/jobs", headers=_auth("tok-a"))
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["a-job"]


def test_cannot_read_another_tenants_job() -> None:
    client = _client(TOKENS, _job("a-job", "tenant-a"), _job("b-job", "tenant-b"))
    # tenant-a's token must not see tenant-b's job.
    assert client.get("/api/v1/ingestion/jobs/b-job", headers=_auth("tok-a")).status_code == 404
    assert client.get("/api/v1/ingestion/jobs/a-job", headers=_auth("tok-a")).status_code == 200
