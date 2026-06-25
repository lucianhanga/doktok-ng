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


def _client(tmp_path: Path) -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test", tenant_tokens=TOKENS, files_root=str(tmp_path), _env_file=None
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
