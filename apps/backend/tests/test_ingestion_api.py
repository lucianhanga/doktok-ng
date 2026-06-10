from datetime import UTC, datetime

from doktok_api.main import create_app
from doktok_contracts.ports import IngestionJobRepository
from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_core.config import Settings
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient


def _client_with_jobs(*jobs: IngestionJob) -> TestClient:
    repo = InMemoryIngestionJobRepository()
    for job in jobs:
        repo.add(job)
    registry = build_registry()
    registry.register(IngestionJobRepository, repo)  # type: ignore[type-abstract]
    app = create_app(settings=Settings(env="test"), registry=registry)
    return TestClient(app)


def _job(job_id: str, sha: str) -> IngestionJob:
    return IngestionJob(
        id=job_id,
        source_path=f"/in.process/{job_id}/source",
        status=JobStatus.NORMALIZING,
        detected_mime="text/plain",
        sha256=sha,
        started_at=datetime.now(UTC),
    )


def test_list_jobs_returns_jobs() -> None:
    client = _client_with_jobs(_job("job-1", "a" * 64), _job("job-2", "b" * 64))
    response = client.get("/api/ingestion/jobs")
    assert response.status_code == 200
    ids = {row["id"] for row in response.json()}
    assert ids == {"job-1", "job-2"}


def test_get_job_by_id() -> None:
    client = _client_with_jobs(_job("job-1", "a" * 64))
    response = client.get("/api/ingestion/jobs/job-1")
    assert response.status_code == 200
    assert response.json()["detected_mime"] == "text/plain"


def test_get_unknown_job_is_404() -> None:
    client = _client_with_jobs()
    response = client.get("/api/ingestion/jobs/missing")
    assert response.status_code == 404
