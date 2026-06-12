"""ThumbnailFeature: renders the first page of the normalized PDF and writes thumbnails/thumb.webp.

The renderer (fitz/Pillow) is faked here so the test needs no native deps; the PyMuPdf adapter is
exercised separately where those libraries are installed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.processors import ThumbnailFeature


class FakeFileStorage:
    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    def read_bytes(self, path: str) -> bytes:
        raise FileNotFoundError(path)

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None:
        self.written[path] = data

    def write_text(self, path: str, text: str) -> None: ...


class FakeThumbnailer:
    def __init__(self) -> None:
        self.seen: str | None = None

    def thumbnail(self, source_path: str, *, max_edge: int = 400) -> bytes:
        self.seen = source_path
        return b"WEBP-bytes"


def _doc(metadata: dict[str, object]) -> Document:
    return Document(
        id="d1",
        tenant_id="t1",
        sha256="x",
        original_filename="report.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path="/store/d1",
        metadata=metadata,
        created_at=datetime.now(UTC),
    )


def test_renders_from_normalized_pdf_and_writes_webp() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc({"system_document": "normalized/searchable.pdf"}))
    files = FakeFileStorage()
    thumbs = FakeThumbnailer()

    ThumbnailFeature(repo, files, thumbs).process("t1", "d1")

    assert thumbs.seen == "/store/d1/normalized/searchable.pdf"  # renders the canonical PDF
    assert files.written == {"/store/d1/thumbnails/thumb.webp": b"WEBP-bytes"}


def test_falls_back_to_original_when_no_system_document() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc({"original": "original.pdf"}))
    files = FakeFileStorage()
    thumbs = FakeThumbnailer()

    ThumbnailFeature(repo, files, thumbs).process("t1", "d1")

    assert thumbs.seen == "/store/d1/original.pdf"
    assert "/store/d1/thumbnails/thumb.webp" in files.written
