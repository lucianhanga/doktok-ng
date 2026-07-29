"""Per-page thumbnail endpoint (#793): serves thumbnails/page-XXXX.webp produced at ingestion."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import DocumentRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(storage_path: str) -> TestClient:
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="report.pdf",
        detected_mime="application/pdf",
        status=DocumentStatus.ACTIVE,
        storage_path=storage_path,
        created_at=datetime.now(UTC),
        metadata={"original": "original.pdf"},
    )
    repo = InMemoryDocumentRepository()
    repo.add(doc)
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_page_thumbnail_served_when_present(tmp_path: Path) -> None:
    thumb = tmp_path / "thumbnails" / "page-0002.webp"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"RIFF....WEBP")
    resp = _client(str(tmp_path)).get("/api/v1/documents/d1/page/2/thumbnail", headers=_auth())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["cache-control"] == "private, max-age=86400"
    assert resp.content == b"RIFF....WEBP"


def test_page_thumbnail_missing_is_404(tmp_path: Path) -> None:
    resp = _client(str(tmp_path)).get("/api/v1/documents/d1/page/1/thumbnail", headers=_auth())
    assert resp.status_code == 404


def test_page_thumbnail_out_of_range_is_404(tmp_path: Path) -> None:
    resp = _client(str(tmp_path)).get("/api/v1/documents/d1/page/0/thumbnail", headers=_auth())
    assert resp.status_code == 404
    resp = _client(str(tmp_path)).get("/api/v1/documents/d1/page/999/thumbnail", headers=_auth())
    assert resp.status_code == 404


def test_page_thumbnail_requires_token(tmp_path: Path) -> None:
    assert _client(str(tmp_path)).get("/api/v1/documents/d1/page/1/thumbnail").status_code == 401
