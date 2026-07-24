"""Rebuild the document registry from files_root manifests (files-ahead-of-DB recovery, #749).

The opposite direction of ``repair.py``: after a disaster where the newest usable pg recovery point
is behind the files tree (or the DB was rebuilt from scratch), every document's bytes can survive in
``<files_root>/<tenant>/docs.active/<document_id>/`` while the ``documents`` rows are gone - and the
UI lists documents from the DB, so the corpus looks lost. Each surviving ``manifest.json`` carries
enough to re-register the row (id, sha256, original filename, mime, timestamps, language, page
count, the artifact map), so this reconciler:

- re-creates the ``documents`` row with the ORIGINAL id, ACTIVE, pointing at the surviving dir;
- records ``extract`` as done (the artifacts are on disk - OCR must NOT re-run) and seeds the
  remaining stage-ledger features as pending, so the normal reconciler re-runs ONLY the enrichment
  that lives in the DB (chunk_embed, entities, ner, entity_graph, relations, doc_metadata,
  doc_classify, structured_records, thumbnail);
- skips ids already present (idempotent re-run) and reports dirs without a valid manifest as
  orphans (never auto-deleted).

``load_manifests`` is injected (the CLI walks the filesystem; tests pass a fake) so this stays a
pure function over the repository ports, mirroring repair.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from doktok_contracts.ports import DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentStatus

# (storage_path, manifest-dict | None) per candidate dir; None = missing/unreadable manifest.
ManifestLoader = Callable[[str], list[tuple[str, dict[str, Any] | None]]]


@dataclass
class RebuildReport:
    scanned: int = 0
    registered: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # dirs without a usable manifest

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} registered={len(self.registered)} "
            f"skipped_existing={len(self.skipped_existing)} orphans={len(self.orphans)}"
        )


def _parse_ts(raw: Any) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(UTC)


def rebuild_registry(
    *,
    document_repo: DocumentRepository,
    feature_repo: FeatureRepository,
    load_manifests: ManifestLoader,
    tenant_id: str,
    stages: list[tuple[str, int]],
    dry_run: bool = False,
) -> RebuildReport:
    """Re-register active documents from on-disk manifests. Returns a RebuildReport.

    ``stages`` is the enrichment stage ledger (everything except ``extract``); each is seeded
    pending for the re-registered document so the reconciler re-derives DB-only state.
    """
    report = RebuildReport()
    for storage_path, manifest in load_manifests(tenant_id):
        report.scanned += 1
        if not manifest:
            report.orphans.append(storage_path)
            continue
        document_id = str(manifest.get("document_id") or Path(storage_path).name)
        if document_repo.get(tenant_id, document_id) is not None:
            report.skipped_existing.append(document_id)
            continue
        created = _parse_ts(manifest.get("created_at"))
        filename = str(manifest.get("original_filename") or f"{document_id}.pdf")
        artifacts = manifest.get("artifacts") or {}
        metadata: dict[str, Any] = {
            "extraction_method": manifest.get("extraction_method"),
            "page_count": manifest.get("page_count"),
            "language": manifest.get("language"),
            "original": artifacts.get("original"),
            "system_document": manifest.get("system_document"),
            # Traceability: this row was rebuilt from files, not ingested (#749).
            "registry_rebuilt": True,
        }
        document = Document(
            id=document_id,
            tenant_id=tenant_id,
            sha256=str(manifest.get("sha256") or ""),
            original_filename=filename,
            detected_mime=manifest.get("detected_mime"),
            title=Path(filename).stem or filename,
            status=DocumentStatus.ACTIVE,
            storage_path=storage_path,
            created_at=created,
            activated_at=created,
            ingested_at=created,
            metadata={k: v for k, v in metadata.items() if v is not None},
        )
        report.registered.append(document_id)
        if not dry_run:
            document_repo.add(document)
            # The artifacts are on disk, so extraction is done by definition; the dependency gate
            # needs the done row before any enrichment feature becomes claimable.
            feature_repo.record_done(tenant_id, document_id, "extract", 1)
            feature_repo.seed_for_document(tenant_id, document_id, stages)
    return report
