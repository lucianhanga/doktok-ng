"""ExtractStage: extract a processing document, write artifacts, and activate it (ADR-0015)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.extraction.service import ExtractionResult
from doktok_core.ingestion.extract_stage import ExtractStage
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_storage_filesystem import LocalFileStorage


def _result() -> tuple[ExtractionResult, None]:
    return (
        ExtractionResult(
            content_md="Hello world",
            pages=["Hello world"],
            extraction_method="pdf_text",
            page_count=1,
        ),
        None,
    )


def _processing(doc_id: str, src: Path, *, sha: str = "a" * 64) -> Document:
    return Document(
        id=doc_id,
        tenant_id="t1",
        sha256=sha,
        original_filename=f"{doc_id}.pdf",
        detected_mime="application/pdf",
        status=DocumentStatus.PROCESSING,
        metadata={"staged_source": str(src)},
        created_at=datetime.now(UTC),
    )


def _stage(tmp_path: Path, repo: InMemoryDocumentRepository) -> ExtractStage:
    files_root = str(tmp_path / "files")
    FilesystemLayout(files_root, "t1").ensure()

    def extractor(mime: str, path: str) -> tuple[ExtractionResult, bytes | None]:
        return _result()

    return ExtractStage(repo, LocalFileStorage(), files_root, extractor)


def test_extracts_writes_artifacts_and_activates(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    repo = InMemoryDocumentRepository()
    repo.add(_processing("d1", src))

    _stage(tmp_path, repo).process("t1", "d1")

    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.status is DocumentStatus.ACTIVE
    assert doc.metadata["extraction_method"] == "pdf_text"
    assert doc.storage_path
    assert (Path(doc.storage_path) / "content.md").read_text(encoding="utf-8") == "Hello world"
    assert not src.exists()  # the original was moved into the document directory


def test_noop_for_a_non_processing_document(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    doc = _processing("d1", src)
    doc.status = DocumentStatus.ACTIVE  # already activated
    repo = InMemoryDocumentRepository()
    repo.add(doc)

    _stage(tmp_path, repo).process("t1", "d1")
    assert src.exists()  # untouched - the stage did nothing


def test_drops_a_document_whose_content_is_already_active(tmp_path: Path) -> None:
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    repo = InMemoryDocumentRepository()
    winner = _processing("d1", tmp_path / "other.pdf")
    winner.status = DocumentStatus.ACTIVE  # already active, same sha
    repo.add(winner)
    repo.add(_processing("d2", src))  # same content, still processing

    _stage(tmp_path, repo).process("t1", "d2")
    assert repo.get("t1", "d2") is None  # the duplicate was dropped, winner kept
    assert repo.get("t1", "d1") is not None
