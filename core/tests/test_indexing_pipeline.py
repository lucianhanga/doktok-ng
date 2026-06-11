"""Pipeline indexing tests (M4) with a fake embedder + in-memory chunk repo (no DB/Ollama)."""

from __future__ import annotations

from pathlib import Path

from doktok_contracts.schemas import JobStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.indexing.inmemory import InMemoryChunkRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"


class FakeMime:
    def detect(self, path: str) -> str:  # noqa: ARG002
        return "text/plain"


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class FailingEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("ollama down")


def _services(
    tmp_path: Path, chunk_repo: InMemoryChunkRepository, embedder: object
) -> IngestionServices:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    return IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMime(),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        chunker=FixedWindowChunker(max_chars=40, overlap=10),
        embedding_provider=embedder,  # type: ignore[arg-type]
        chunk_repo=chunk_repo,
    )


def test_active_document_is_chunked_and_embedded(tmp_path: Path) -> None:
    repo = InMemoryChunkRepository()
    services = _services(tmp_path, repo, FakeEmbedder())
    (services.layout.ingest / "doc.txt").write_bytes(b"abcdefghij" * 10)

    job = process_file(services, str(services.layout.ingest / "doc.txt"))

    assert job.status is JobStatus.ACTIVE
    assert len(repo.chunks) > 1
    assert len(repo.embeddings) == len(repo.chunks)
    assert all(c.tenant_id == TENANT and c.document_id == job.document_id for c in repo.chunks)
    doc = services.document_repo.get(TENANT, job.document_id)  # type: ignore[arg-type]
    assert doc is not None
    assert all(c.document_id == doc.id for c in repo.chunks)


def test_indexing_failure_fails_the_job(tmp_path: Path) -> None:
    repo = InMemoryChunkRepository()
    services = _services(tmp_path, repo, FailingEmbedder())
    (services.layout.ingest / "doc.txt").write_bytes(b"some content here")

    job = process_file(services, str(services.layout.ingest / "doc.txt"))

    assert job.status is JobStatus.FAILED
    assert job.error_code == "indexing_error"
    assert repo.chunks == []
