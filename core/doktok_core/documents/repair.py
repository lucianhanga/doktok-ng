"""Post-restore reconciliation of the document DB against the files_root tree (APP-C2).

After a restore (or a crash), an active ``documents`` row can point at artifacts that are missing on
disk - the dangerous "DB ahead of files" direction. This reconciler walks active documents and:

- original file present + all derived artifacts present  -> OK, nothing to do
- original present but some DERIVED artifacts missing    -> re-queue the document's feature ledger
  rows so the reconciler rebuilds them from the original (idempotent re-derivation)
- original file MISSING                                  -> unrecoverable (the only irreplaceable
  bytes are gone); reported for an operator decision, never auto-deleted

``exists`` is injected (the CLI passes ``Path.exists``; tests pass a set-backed fake) so this stays
a pure, testable function over the repository ports with no filesystem coupling.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from doktok_contracts.ports import DocumentRepository, FeatureRepository
from doktok_contracts.schemas import DocumentStatus

from doktok_core.documents.artifacts import NORMALIZED_PDF_REL, THUMBNAIL_REL

# Artifacts every active document should have on disk (besides the original + system_document).
_REQUIRED_RELS = ("content.md", "manifest.json", THUMBNAIL_REL)


@dataclass
class RepairReport:
    checked: int = 0
    ok: int = 0
    repaired: list[str] = field(default_factory=list)  # doc ids re-queued for re-derivation
    unrecoverable: list[str] = field(default_factory=list)  # doc ids whose original is gone

    def summary(self) -> str:
        return (
            f"checked={self.checked} ok={self.ok} repaired={len(self.repaired)} "
            f"unrecoverable={len(self.unrecoverable)}"
        )


def _artifact_paths(base: str, original_rel: str | None, system_doc: str | None) -> list[str]:
    rels = [original_rel or "", system_doc or NORMALIZED_PDF_REL, *_REQUIRED_RELS]
    return [f"{base.rstrip('/')}/{rel}" for rel in rels if rel]


def repair_documents(
    *,
    document_repo: DocumentRepository,
    feature_repo: FeatureRepository,
    exists: Callable[[str], bool],
    tenant_id: str,
    dry_run: bool = False,
) -> RepairReport:
    """Reconcile active documents against their on-disk artifacts. Returns a RepairReport."""
    report = RepairReport()
    cursor = None
    while True:
        docs, _total, anchor = document_repo.list_documents(
            tenant_id, status=DocumentStatus.ACTIVE, limit=200, cursor=cursor
        )
        for doc in docs:
            report.checked += 1
            base = doc.storage_path or ""
            original_rel = str(doc.metadata.get("original") or "") or None
            system_doc = str(doc.metadata.get("system_document") or "") or None
            original_path = f"{base.rstrip('/')}/{original_rel}" if base and original_rel else ""

            if not original_path or not exists(original_path):
                report.unrecoverable.append(doc.id)  # original gone -> cannot re-derive
                continue

            missing = [p for p in _artifact_paths(base, original_rel, system_doc) if not exists(p)]
            if not missing:
                report.ok += 1
                continue

            # Derived artifacts missing but the original survives -> re-queue features to rebuild.
            report.repaired.append(doc.id)
            if not dry_run:
                for feature in feature_repo.list_for_document(tenant_id, doc.id):
                    feature_repo.reset(tenant_id, doc.id, feature.feature)
        if anchor is None:
            break
        cursor = anchor
    return report
