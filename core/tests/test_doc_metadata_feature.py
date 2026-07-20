"""DocMetadataFeature: reads content.md, extracts + normalizes, writes document columns (M6.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.media import ExtractedMetadata
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.processors import DocMetadataFeature


class FakeFileStorage:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def write_text(self, path: str, text: str) -> None: ...


class FakeExtractor:
    def __init__(self, result: ExtractedMetadata) -> None:
        self.result = result
        self.seen: str | None = None

    def extract(self, text: str) -> ExtractedMetadata:
        self.seen = text
        return self.result


def _doc() -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="report.pdf",
        title="report",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/d1",
        created_at=datetime.now(UTC),
    )


def test_writes_normalized_metadata_to_document() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    files = FakeFileStorage({"/store/d1/content.md": b"Annual report for Acme, dated 2026-01-15."})
    extractor = FakeExtractor(
        ExtractedMetadata(
            title="Acme Annual Report 2026",
            document_date="2026-01-15",
            location="n/a",
            summary="Acme's annual financial report.",
        )
    )
    DocMetadataFeature(repo, files, lambda _t: extractor).process("t1", "d1")

    doc = repo.get("t1", "d1")
    assert doc is not None
    assert doc.title == "Acme Annual Report 2026"
    assert doc.document_date is not None and doc.document_date.isoformat() == "2026-01-15"
    assert doc.location is None  # "n/a" normalized to None
    assert doc.summary == "Acme's annual financial report."
    assert "Annual report" in (extractor.seen or "")


def test_skips_when_no_content() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc())
    extractor = FakeExtractor(ExtractedMetadata("x", None, None, "y"))
    DocMetadataFeature(repo, FakeFileStorage({}), lambda _t: extractor).process("t1", "d1")
    assert extractor.seen is None  # never called the model
    assert repo.get("t1", "d1").summary is None  # type: ignore[union-attr]
