"""ThumbnailFeature v2 (#793): first-page card thumbnail PLUS one small WebP per page.

The renderer (fitz/Pillow) is faked here so the test needs no native deps; the PyMuPdf adapter is
exercised separately where those libraries are installed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.processors import ThumbnailFeature


class FakeFileStorage:
    def __init__(self, manifest: dict[str, object] | None = None) -> None:
        self.written: dict[str, bytes] = {}
        self._manifest = manifest

    def read_bytes(self, path: str) -> bytes:
        if self._manifest is not None and path.endswith("manifest.json"):
            return json.dumps(self._manifest).encode("utf-8")
        raise FileNotFoundError(path)

    def move(self, source: str, destination: str) -> None: ...
    def write_bytes(self, path: str, data: bytes) -> None:
        self.written[path] = data

    def write_text(self, path: str, text: str) -> None: ...


class FakeThumbnailer:
    def __init__(self) -> None:
        self.seen: list[tuple[str, int]] = []
        self.first_seen: str | None = None

    def thumbnail(self, source_path: str, *, max_edge: int = 400) -> bytes:
        self.first_seen = source_path
        return b"WEBP-first"

    def page_thumbnail(self, source_path: str, page_index: int, *, max_edge: int = 320) -> bytes:
        self.seen.append((source_path, page_index))
        return f"WEBP-p{page_index + 1}".encode()


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

    assert thumbs.first_seen == "/store/d1/normalized/searchable.pdf"
    assert files.written == {"/store/d1/thumbnails/thumb.webp": b"WEBP-first"}


def test_falls_back_to_original_when_no_system_document() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc({"original": "original.pdf"}))
    files = FakeFileStorage()
    thumbs = FakeThumbnailer()

    ThumbnailFeature(repo, files, thumbs).process("t1", "d1")

    assert thumbs.first_seen == "/store/d1/original.pdf"
    assert "/store/d1/thumbnails/thumb.webp" in files.written


def test_writes_one_thumbnail_per_page_and_registers_the_manifest() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc({"system_document": "normalized/searchable.pdf", "page_count": 3}))
    manifest: dict[str, object] = {"document_id": "d1", "artifacts": {"original": "original.pdf"}}
    files = FakeFileStorage(manifest=manifest)
    thumbs = FakeThumbnailer()

    feature = ThumbnailFeature(repo, files, thumbs)
    feature.process("t1", "d1")

    assert feature.version == 2  # the backfill trigger for existing documents
    assert thumbs.seen == [
        ("/store/d1/normalized/searchable.pdf", 0),
        ("/store/d1/normalized/searchable.pdf", 1),
        ("/store/d1/normalized/searchable.pdf", 2),
    ]
    expected = {
        "/store/d1/thumbnails/thumb.webp",
        "/store/d1/thumbnails/page-0001.webp",
        "/store/d1/thumbnails/page-0002.webp",
        "/store/d1/thumbnails/page-0003.webp",
    }
    assert set(files.written) == expected | {"/store/d1/manifest.json"}
    written_manifest = json.loads(files.written["/store/d1/manifest.json"])
    assert written_manifest["artifacts"]["page_thumbnails"] == [
        "thumbnails/page-0001.webp",
        "thumbnails/page-0002.webp",
        "thumbnails/page-0003.webp",
    ]


def test_no_page_count_means_card_thumbnail_only() -> None:
    repo = InMemoryDocumentRepository()
    repo.add(_doc({"system_document": "normalized/searchable.pdf"}))
    files = FakeFileStorage()
    thumbs = FakeThumbnailer()

    ThumbnailFeature(repo, files, thumbs).process("t1", "d1")

    assert thumbs.seen == []  # no per-page work without a count
    assert list(files.written) == ["/store/d1/thumbnails/thumb.webp"]
