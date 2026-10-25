"""Manual retry of a failed/quarantined ingestion job: the stranded source file is moved back
into the tenant ingest folder so the worker's normal scan picks it up as a fresh job (#848)."""

from pathlib import Path

from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, IngestionJobRepository
from doktok_contracts.schemas import IngestionJob, JobStatus
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


def _client(tmp_path: Path, jobs: list[IngestionJob]) -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    job_repo = InMemoryIngestionJobRepository()
    for job in jobs:
        job_repo.add(job)
    registry.register(IngestionJobRepository, job_repo)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens=TOKENS,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def _stranded_job(tmp_path: Path, **overrides: object) -> IngestionJob:
    stranded = tmp_path / "tenant-a" / "processed" / "abc123"
    stranded.mkdir(parents=True, exist_ok=True)
    src = stranded / "source.pdf"
    src.write_bytes(b"%PDF-1.4 retried")
    fields: dict[str, object] = {
        "id": "job-1",
        "tenant_id": "tenant-a",
        "source_path": str(src),
        "status": JobStatus.FAILED,
        "error_code": "worker_crash_recovered",
        "metadata": {"original_ingest_path": "/data/ingest/report.pdf"},
    }
    fields.update(overrides)
    return IngestionJob(**fields)  # type: ignore[arg-type]


def test_retry_moves_the_stranded_file_back_into_ingest(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path)
    client = _client(tmp_path, [job])
    resp = client.post(f"/api/v1/ingestion/jobs/{job.id}/retry", headers=AUTH)
    assert resp.status_code == 200
    # Back in the ingest folder under the ORIGINAL filename; the stranded copy is gone.
    assert (tmp_path / "tenant-a" / "ingest" / "report.pdf").read_bytes() == b"%PDF-1.4 retried"
    assert not Path(job.source_path).exists()


def test_retry_unknown_job_is_404(tmp_path: Path) -> None:
    resp = _client(tmp_path, []).post("/api/v1/ingestion/jobs/nope/retry", headers=AUTH)
    assert resp.status_code == 404


def test_retry_refuses_a_non_failed_job(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path, status=JobStatus.ACTIVE)
    client = _client(tmp_path, [job])
    resp = client.post(f"/api/v1/ingestion/jobs/{job.id}/retry", headers=AUTH)
    assert resp.status_code == 409
    # The file stays put - nothing was requeued.
    assert Path(job.source_path).exists()


def test_retry_without_the_source_file_is_a_clear_409(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path)
    Path(job.source_path).unlink()  # file was cleaned up since
    client = _client(tmp_path, [job])
    resp = client.post(f"/api/v1/ingestion/jobs/{job.id}/retry", headers=AUTH)
    assert resp.status_code == 409
    assert "re-upload" in resp.json()["detail"]


def test_retry_requires_a_token(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path)
    resp = _client(tmp_path, [job]).post(f"/api/v1/ingestion/jobs/{job.id}/retry")
    assert resp.status_code == 401


def test_dismiss_removes_a_failed_tombstone(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path)
    client = _client(tmp_path, [job])
    resp = client.delete(f"/api/v1/ingestion/jobs/{job.id}", headers=AUTH)
    assert resp.status_code == 204
    remaining = client.get("/api/v1/ingestion/jobs", headers=AUTH).json()
    assert all(j["id"] != job.id for j in remaining)


def test_dismiss_unknown_job_is_404(tmp_path: Path) -> None:
    resp = _client(tmp_path, []).delete("/api/v1/ingestion/jobs/nope", headers=AUTH)
    assert resp.status_code == 404


def test_dismiss_refuses_an_in_flight_job(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path, status=JobStatus.EXTRACTING)
    client = _client(tmp_path, [job])
    resp = client.delete(f"/api/v1/ingestion/jobs/{job.id}", headers=AUTH)
    assert resp.status_code == 409


def test_dismiss_requires_a_token(tmp_path: Path) -> None:
    job = _stranded_job(tmp_path)
    resp = _client(tmp_path, [job]).delete(f"/api/v1/ingestion/jobs/{job.id}")
    assert resp.status_code == 401
