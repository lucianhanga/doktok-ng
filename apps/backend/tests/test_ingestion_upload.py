"""Drag-and-drop document upload -> tenant ingest folder (M14 #370)."""

from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


def _client(tmp_path: Path, *, max_upload_files: int = 101, max_request_mb: int = 25) -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
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


def test_oversized_file_is_rejected_without_a_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # F-12: read-then-check made the whole file RAM-resident before the size cap fired. The route
    # now streams in bounded chunks and aborts as soon as the cap is crossed.
    from starlette.datastructures import UploadFile as StarletteUploadFile

    limit = 25 * 1024 * 1024
    chunk = 1024 * 1024
    read_bytes = 0
    real_read = StarletteUploadFile.read

    async def _counting_read(self: StarletteUploadFile, size: int = -1) -> bytes:
        nonlocal read_bytes
        data = await real_read(self, size)
        read_bytes += len(data)
        return data

    monkeypatch.setattr(StarletteUploadFile, "read", _counting_read)
    client = _client(tmp_path)  # max_request_mb=25
    big = b"x" * (limit + 5 * chunk)  # 5 MiB over the cap
    resp = client.post(
        "/api/v1/ingestion/upload",
        files=[("files", ("big.bin", big, "application/octet-stream"))],
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"] == ["big.bin: exceeds 25 MB"]
    # The read stopped as soon as the cap was crossed - not the whole 30 MiB.
    assert read_bytes <= limit + chunk
    ingest = tmp_path / "tenant-a" / "ingest"
    assert not (ingest / "big.bin").exists()
    assert list(ingest.glob(".upload-*")) == []
