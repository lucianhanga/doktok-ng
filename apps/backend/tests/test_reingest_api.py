"""POST /api/v1/documents/{id}/reingest: re-queue a failed document (tmp filesystem)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import DocumentRepository, IngestionJobRepository
from doktok_contracts.schemas import Document, DocumentStatus, IngestionJob, JobStatus
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
TENANT = "tenant-a"
AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(
    files_root: Path,
    docs: InMemoryDocumentRepository,
    job_repo: InMemoryIngestionJobRepository,
) -> TestClient:
    registry = build_registry()
    registry.register(DocumentRepository, docs)  # type: ignore[type-abstract]
    registry.register(IngestionJobRepository, job_repo)  # type: ignore[type-abstract]
    settings = Settings(
        env="test", tenant_tokens=TOKENS, files_root=str(files_root), _env_file=None
    )  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _failed_doc(storage_path: str, status: DocumentStatus = DocumentStatus.FAILED) -> Document:
    return Document(
        id="d1",
        tenant_id=TENANT,
        sha256="a" * 64,
        original_filename="report.pdf",
        status=status,
        storage_path=storage_path,
        created_at=datetime.now(UTC),
    )


def test_reingest_moves_file_and_clears_records(tmp_path: Path) -> None:
    failed_dir = tmp_path / TENANT / "docs.failed" / "guid1"
    failed_dir.mkdir(parents=True)
    (failed_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")

    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(failed_dir)))
    job_repo = InMemoryIngestionJobRepository()
    job_repo.add(
        IngestionJob(
            id="j1",
            tenant_id=TENANT,
            document_id="d1",
            source_path="/x",
            sha256="a" * 64,
            status=JobStatus.FAILED,
        )
    )

    resp = _client(tmp_path, docs, job_repo).post("/api/v1/documents/d1/reingest", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["filename"] == "report.pdf"

    assert (tmp_path / TENANT / "ingest" / "report.pdf").is_file()  # moved to ingest
    assert not failed_dir.exists()  # failed folder removed
    assert docs.get(TENANT, "d1") is None  # document record cleared
    assert job_repo.get(TENANT, "j1") is None  # the document's own failed job cleared


def test_reingest_active_document_purges_and_requeues(tmp_path: Path) -> None:
    active_dir = tmp_path / TENANT / "docs.active" / "guid1"
    active_dir.mkdir(parents=True)
    (active_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(active_dir), status=DocumentStatus.ACTIVE))
    job_repo = InMemoryIngestionJobRepository()
    job_repo.add(
        IngestionJob(
            id="j1",
            tenant_id=TENANT,
            document_id="d1",
            source_path="/x",
            sha256="a" * 64,
            status=JobStatus.ACTIVE,
        )
    )

    resp = _client(tmp_path, docs, job_repo).post("/api/v1/documents/d1/reingest", headers=AUTH)
    assert resp.status_code == 200  # active docs can be re-ingested now
    assert (tmp_path / TENANT / "ingest" / "report.pdf").is_file()
    assert not active_dir.exists()
    assert docs.get(TENANT, "d1") is None
    assert job_repo.get(TENANT, "j1") is None  # the document's own job is purged


def test_delete_does_not_purge_another_documents_job(tmp_path: Path) -> None:
    # Two documents share the same content hash (e.g. an active document plus a stale failed twin).
    # Deleting one must remove only its own job, never the other document's job (regression for the
    # delete-by-content-hash bug that left active documents with no ingestion job).
    d1_dir = tmp_path / TENANT / "docs.failed" / "guid1"
    d1_dir.mkdir(parents=True)
    (d1_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(d1_dir)))  # id "d1", sha "a"*64
    docs.add(
        Document(
            id="d2",
            tenant_id=TENANT,
            sha256="a" * 64,  # same content as d1
            original_filename="report.pdf",
            status=DocumentStatus.ACTIVE,
            storage_path=str(tmp_path),
            created_at=datetime.now(UTC),
        )
    )
    job_repo = InMemoryIngestionJobRepository()
    job_repo.add(
        IngestionJob(
            id="j1",
            tenant_id=TENANT,
            document_id="d1",
            source_path="/x",
            sha256="a" * 64,
            status=JobStatus.FAILED,
        )
    )
    job_repo.add(
        IngestionJob(
            id="j2",
            tenant_id=TENANT,
            document_id="d2",
            source_path="/y",
            sha256="a" * 64,
            status=JobStatus.ACTIVE,
        )
    )

    resp = _client(tmp_path, docs, job_repo).delete("/api/v1/documents/d1", headers=AUTH)
    assert resp.status_code == 200

    assert job_repo.get(TENANT, "j1") is None  # the deleted document's job is gone
    assert job_repo.get(TENANT, "j2") is not None  # the other document's job survives
    assert docs.get(TENANT, "d2") is not None  # and so does the other document


def test_reingest_enhanced_routes_to_the_enhanced_ingest_folder(tmp_path: Path) -> None:
    active_dir = tmp_path / TENANT / "docs.active" / "guid1"
    active_dir.mkdir(parents=True)
    (active_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(active_dir), status=DocumentStatus.ACTIVE))

    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).post(
        "/api/v1/documents/d1/reingest?profile=enhanced", headers=AUTH
    )
    assert resp.status_code == 200 and resp.json()["profile"] == "enhanced"
    # routed to the enhanced intake folder, not the standard one
    assert (tmp_path / TENANT / "ingest.enhanced" / "report.pdf").is_file()
    assert not (tmp_path / TENANT / "ingest" / "report.pdf").exists()


def test_rotate_document_requeues_a_rotated_pdf(tmp_path: Path) -> None:
    import fitz

    active_dir = tmp_path / TENANT / "docs.active" / "guid1"
    active_dir.mkdir(parents=True)
    doc = fitz.open()
    doc.new_page()
    (active_dir / "report.pdf").write_bytes(doc.tobytes())
    doc.close()

    docs = InMemoryDocumentRepository()
    record = _failed_doc(str(active_dir), status=DocumentStatus.ACTIVE).model_copy(
        update={"detected_mime": "application/pdf"}
    )
    docs.add(record)

    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).post(
        "/api/v1/documents/d1/rotate?degrees=90", headers=AUTH
    )
    assert resp.status_code == 200 and resp.json()["degrees"] == "90"

    requeued = tmp_path / TENANT / "ingest" / "report.pdf"
    assert requeued.is_file()
    with fitz.open(stream=requeued.read_bytes(), filetype="pdf") as rotated:
        assert rotated[0].rotation == 90  # the re-queued source renders upright for re-OCR
    assert docs.get(TENANT, "d1") is None  # purged so the worker reprocesses it


def test_rotate_requires_token(tmp_path: Path) -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(tmp_path)))
    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).post(
        "/api/v1/documents/d1/rotate"
    )
    assert resp.status_code == 401


def test_reingest_requires_token(tmp_path: Path) -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(tmp_path)))
    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).post(
        "/api/v1/documents/d1/reingest"
    )
    assert resp.status_code == 401


def test_delete_removes_file_and_record(tmp_path: Path) -> None:
    failed_dir = tmp_path / TENANT / "docs.failed" / "guid1"
    failed_dir.mkdir(parents=True)
    (failed_dir / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(failed_dir)))

    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).delete(
        "/api/v1/documents/d1", headers=AUTH
    )
    assert resp.status_code == 200 and resp.json()["status"] == "deleted"
    assert not failed_dir.exists()  # files removed
    assert docs.get(TENANT, "d1") is None  # record removed


def test_delete_requires_token(tmp_path: Path) -> None:
    docs = InMemoryDocumentRepository()
    docs.add(_failed_doc(str(tmp_path)))
    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).delete("/api/v1/documents/d1")
    assert resp.status_code == 401


def test_delete_is_idempotent_for_missing_document(tmp_path: Path) -> None:
    # A retried DELETE of an already-removed document succeeds (idempotent), not 404.
    docs = InMemoryDocumentRepository()
    resp = _client(tmp_path, docs, InMemoryIngestionJobRepository()).delete(
        "/api/v1/documents/gone", headers=AUTH
    )
    assert resp.status_code == 200 and resp.json()["status"] == "deleted"
