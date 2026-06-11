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


def _client(storage_path: str, *, metadata: dict[str, object] | None = None) -> TestClient:
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="report.txt",
        detected_mime="text/plain",
        status=DocumentStatus.ACTIVE,
        storage_path=storage_path,
        created_at=datetime.now(UTC),
        metadata=metadata or {"original": "original.txt"},
    )
    repo = InMemoryDocumentRepository()
    repo.add(doc)
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_requires_token() -> None:
    assert _client("/tmp").get("/api/v1/documents/d1/file").status_code == 401


def test_serves_original_inline_with_hardening_headers(tmp_path: Path) -> None:
    (tmp_path / "original.txt").write_text("hello file body", encoding="utf-8")
    resp = _client(str(tmp_path)).get("/api/v1/documents/d1/file", headers=_auth())
    assert resp.status_code == 200
    assert resp.text == "hello file body"
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_attachment_disposition(tmp_path: Path) -> None:
    (tmp_path / "original.txt").write_text("x", encoding="utf-8")
    resp = _client(str(tmp_path)).get(
        "/api/v1/documents/d1/file?disposition=attachment", headers=_auth()
    )
    assert resp.headers["content-disposition"].startswith("attachment")


def test_normalized_variant_missing_is_404(tmp_path: Path) -> None:
    (tmp_path / "original.txt").write_text("x", encoding="utf-8")
    # metadata has no system_document -> normalized not available
    resp = _client(str(tmp_path)).get(
        "/api/v1/documents/d1/file?variant=normalized", headers=_auth()
    )
    assert resp.status_code == 404


def test_unknown_document_is_404(tmp_path: Path) -> None:
    resp = _client(str(tmp_path)).get("/api/v1/documents/missing/file", headers=_auth())
    assert resp.status_code == 404
