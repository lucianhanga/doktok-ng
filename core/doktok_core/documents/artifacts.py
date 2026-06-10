"""Write canonical document artifacts to docs.active/{document_id}/ (brief section 14)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from doktok_contracts.ports import FileStorage

from doktok_core.extraction.service import ExtractionResult
from doktok_core.ingestion.layout import FilesystemLayout


def write_document_artifacts(
    file_storage: FileStorage,
    layout: FilesystemLayout,
    document_id: str,
    *,
    original_source_path: str,
    original_filename: str,
    sha256: str,
    detected_mime: str | None,
    detector: str,
    result: ExtractionResult,
) -> str:
    """Materialize original + content.md/json + pages/ + manifest.json. Returns the active dir."""
    active_dir = layout.active_dir(document_id)
    pages_rel = [f"pages/page-{i:04d}.json" for i in range(1, result.page_count + 1)]
    artifacts = ["original", "content.md", "content.json", *pages_rel]

    # Move the validated source into the document's canonical directory.
    file_storage.move(original_source_path, str(active_dir / "original"))

    file_storage.write_text(str(active_dir / "content.md"), result.content_md)

    content_json = {
        "document_id": document_id,
        "extraction_method": result.extraction_method,
        "page_count": result.page_count,
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

    manifest = {
        "document_id": document_id,
        "version_id": None,
        "sha256": sha256,
        "original_filename": original_filename,
        "detected_mime": detected_mime,
        "detector": detector,
        "created_at": datetime.now(UTC).isoformat(),
        "extraction_method": result.extraction_method,
        "page_count": result.page_count,
        "language": "unknown",
        "artifacts": artifacts,
    }
    file_storage.write_text(
        str(active_dir / "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2)
    )

    return str(active_dir)
