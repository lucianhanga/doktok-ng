"""Drag-and-drop document upload -> tenant ingest folder (M14 #370)."""

from pathlib import Path

from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


def _client(tmp_path: Path, *, max_upload_files: int = 101, max_request_mb: int = 25) -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens=TOKENS,
        files_root=str(tmp_path),
        max_upload_files=max_upload_files,
        max_request_mb=max_request_mb,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_upload_writes_files_into_the_tenant_ingest_folder(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("invoice.pdf", b"%PDF-1.4 hello", "application/pdf"))],
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == ["invoice.pdf"] and body["rejected"] == []
    ingest = tmp_path / "tenant-a" / "ingest"
    assert (ingest / "invoice.pdf").read_bytes() == b"%PDF-1.4 hello"
    # No partial/temp dotfiles left behind (the worker ignores dotfiles; the publish is atomic).
    assert list(ingest.glob(".upload-*")) == []


def test_upload_requires_a_token(tmp_path: Path) -> None:
    resp = _client(tmp_path).post(
        "/api/v1/ingestion/upload",
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert resp.status_code == 401


def test_upload_neutralizes_path_traversal_filenames(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("../../etc/passwd", b"x", "text/plain"))],
        headers=AUTH,
    )
    assert resp.status_code == 200
    # The name is reduced to a safe basename and written INSIDE the ingest folder, never outside.
    assert resp.json()["accepted"] == ["passwd"]
    assert (tmp_path / "tenant-a" / "ingest" / "passwd").exists()
    assert not (tmp_path / "etc").exists()


def test_upload_rejects_the_whole_batch_over_the_file_count_limit(tmp_path: Path) -> None:
    client = _client(tmp_path, max_upload_files=2)
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(3)]
    resp = client.post("/api/v1/ingestion/upload", files=files, headers=AUTH)
    assert resp.status_code == 400
    assert "at most 2 files" in resp.json()["detail"]
    # Nothing was written - the whole batch is refused, not partially accepted.
    ingest = tmp_path / "tenant-a" / "ingest"
    assert not ingest.exists() or list(ingest.glob("*")) == []


def test_upload_accepts_good_files_and_rejects_only_the_oversized_one(tmp_path: Path) -> None:
    # Per-file cap = max_request_mb (1 MB here). A file over it is rejected on its own; the rest go.
    client = _client(tmp_path, max_request_mb=1)
    big = b"x" * (2 * 1024 * 1024)  # 2 MB > 1 MB per-file cap
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[
            ("files", ("big.pdf", big, "application/pdf")),
            ("files", ("small.txt", b"ok", "text/plain")),
        ],
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == ["small.txt"]
    assert any("big.pdf" in r and "exceeds 1 MB" in r for r in body["rejected"])
    ingest = tmp_path / "tenant-a" / "ingest"
    assert (ingest / "small.txt").exists() and not (ingest / "big.pdf").exists()


def test_upload_rejects_an_empty_file(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("empty.txt", b"", "text/plain"))],
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == [] and any("empty" in r for r in body["rejected"])
