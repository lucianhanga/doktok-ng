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

    def extractor(tenant_id: str, mime: str, path: str) -> tuple[ExtractionResult, bytes | None]:
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


def test_render_limit_fails_the_document_terminally(tmp_path: Path) -> None:
    # A render bound violation is deterministic: the document must end FAILED with the error
    # recorded, not propagate for the reconciler to retry until max_attempts.
    from doktok_contracts.errors import RenderLimitExceededError

    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    repo = InMemoryDocumentRepository()
    repo.add(_processing("d1", src))

    files_root = str(tmp_path / "files")
    FilesystemLayout(files_root, "t1").ensure()

    def extractor(tenant_id: str, mime: str, path: str) -> tuple[ExtractionResult, bytes | None]:
        raise RenderLimitExceededError("page exceeds pixel cap")

    ExtractStage(repo, LocalFileStorage(), files_root, extractor).process("t1", "d1")

    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.status is DocumentStatus.FAILED
    assert doc.metadata["error_code"] == "render_limit_exceeded"
    assert "pixel cap" in doc.metadata["error_message"]


def test_missing_source_fails_the_document_terminally(tmp_path: Path) -> None:
    # The staged source file is gone AND no artifacts from a previous attempt survive to resume
    # from: retrying can never succeed, so the stage fails the document terminally instead of
    # letting the reconciler burn max_attempts while the document sits 'processing'.
    src = tmp_path / "gone.pdf"  # never written
    repo = InMemoryDocumentRepository()
    repo.add(_processing("d1", src))

    files_root = str(tmp_path / "files")
    FilesystemLayout(files_root, "t1").ensure()

    def extractor(tenant_id: str, mime: str, path: str) -> tuple[ExtractionResult, bytes | None]:
        raise AssertionError("extractor must not run without a source file")

    ExtractStage(repo, LocalFileStorage(), files_root, extractor).process("t1", "d1")

    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.status is DocumentStatus.FAILED
    assert doc.metadata["error_code"] == "source_missing"
    assert "no such file" in doc.metadata["error_message"]


def test_resumes_when_a_killed_attempt_already_moved_the_source(tmp_path: Path) -> None:
    # Crash window: a previous attempt finished write_document_artifacts (which moves the source
    # into docs.active/<doc_id>/) but died before activation. The retry must recover the original
    # from the artifact directory and complete - not fail with "no such file".
    layout = FilesystemLayout(str(tmp_path / "files"), "t1")
    layout.ensure()
    active_dir = layout.active_dir("d1")
    active_dir.mkdir(parents=True)
    (active_dir / "original.pdf").write_bytes(b"%PDF-1.4 recovered")

    staged = layout.job_workdir("job-1") / "source.pdf"  # gone: moved out by the killed attempt
    repo = InMemoryDocumentRepository()
    repo.add(_processing("d1", staged))

    _stage(tmp_path, repo).process("t1", "d1")

    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.status is DocumentStatus.ACTIVE
    # The re-extracted original is back in the canonical directory.
    assert (Path(doc.storage_path) / "original.pdf").is_file()
