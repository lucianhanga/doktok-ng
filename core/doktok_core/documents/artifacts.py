"""Write canonical document artifacts to docs.active/{document_id}/ (brief section 14).

Layout:

    docs.active/{document_id}/
      original.<ext>          the user's file, kept with its real extension (openable)
      manifest.json           metadata + which artifact is the canonical "system document"
      content.md              canonical extracted text (plain UTF-8; used for chunking/embeddings)
      content.json            structured extraction (pages, method)
      pages/page-NNNN.json    per-page structured text
      normalized/
        searchable.pdf        derived OCR'd PDF (images + text layer); created by OCR in M3

The "system document" is the canonical openable representation: the OCR'd
``normalized/searchable.pdf`` when present (scanned input), otherwise the ``original.<ext>``.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.ports import FileStorage

from doktok_core.extraction.service import ExtractionResult
from doktok_core.ingestion.layout import FilesystemLayout

# Extension is taken from the (trusted) detected MIME, not the untrusted filename.
_MIME_EXTENSION = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
    "image/webp": ".webp",
}

NORMALIZED_PDF_REL = "normalized/searchable.pdf"


def extension_for(mime: str | None, original_filename: str) -> str:
    """Choose a file extension from the detected MIME, falling back to the filename suffix."""
    if mime and mime in _MIME_EXTENSION:
        return _MIME_EXTENSION[mime]
    if mime:
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return guessed
    return Path(original_filename).suffix


@dataclass
class ArtifactResult:
    storage_path: str
    original: str
    system_document: str


def write_document_artifacts(
    file_storage: FileStorage,
    layout: FilesystemLayout,
    document_id: str,
    *,
    tenant_id: str,
    original_source_path: str,
    original_filename: str,
    sha256: str,
    detected_mime: str | None,
    detector: str,
    result: ExtractionResult,
    normalized_pdf: bytes | None = None,
) -> ArtifactResult:
    """Materialize the canonical artifacts. Returns the storage dir and key relative paths."""
    active_dir = layout.active_dir(document_id)
    ext = extension_for(detected_mime, original_filename)
    original_rel = f"original{ext}"
    pages_rel = [f"pages/page-{i:04d}.json" for i in range(1, result.page_count + 1)]

    # Move the validated source into the document's canonical directory (keep its extension).
    file_storage.move(original_source_path, str(active_dir / original_rel))

    # content.md is the plain-text canonical content used for chunking and embeddings (M4).
    file_storage.write_text(str(active_dir / "content.md"), result.content_md)

    content_json = {
        "document_id": document_id,
        "extraction_method": result.extraction_method,
        "page_count": result.page_count,
        "ocr_confidence": result.ocr_confidence,
        "pages": [{"page_number": i, "text": text} for i, text in enumerate(result.pages, start=1)],
        "metadata": result.metadata,
    }
    file_storage.write_text(
        str(active_dir / "content.json"), json.dumps(content_json, ensure_ascii=False, indent=2)
    )

    for i, text in enumerate(result.pages, start=1):
        page_json = {
            "page_number": i,
            "text": text,
            "extraction_method": result.extraction_method,
        }
        file_storage.write_text(
            str(active_dir / f"pages/page-{i:04d}.json"),
            json.dumps(page_json, ensure_ascii=False, indent=2),
        )

    normalized_rel: str | None = None
    if normalized_pdf is not None:
        normalized_rel = NORMALIZED_PDF_REL
        file_storage.write_bytes(str(active_dir / normalized_rel), normalized_pdf)

    system_document = normalized_rel or original_rel

    manifest = {
        "document_id": document_id,
        "tenant_id": tenant_id,
        "version_id": None,
        "sha256": sha256,
        "original_filename": original_filename,
        "detected_mime": detected_mime,
        "detector": detector,
        "created_at": datetime.now(UTC).isoformat(),
        "extraction_method": result.extraction_method,
        "page_count": result.page_count,
        "language": "unknown",
        "system_document": system_document,
        "artifacts": {
            "original": original_rel,
            "content_md": "content.md",
            "content_json": "content.json",
            "pages": pages_rel,
            "normalized_pdf": normalized_rel,
        },
    }
    file_storage.write_text(
        str(active_dir / "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2)
    )

    return ArtifactResult(
        storage_path=str(active_dir), original=original_rel, system_document=system_document
    )
