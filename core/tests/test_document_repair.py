"""Post-restore repair reconciles active documents against on-disk artifacts (APP-C2)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.documents.repair import repair_documents
from doktok_core.features.inmemory import InMemoryFeatureRepository

TENANT = "t"


def _doc(doc_id: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=TENANT,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.pdf",
        status=DocumentStatus.ACTIVE,
        storage_path=f"/files/{doc_id}",
        metadata={"original": "original.pdf", "system_document": "normalized/searchable.pdf"},
        created_at=datetime.now(UTC),
    )


def _all_artifacts(doc_id: str) -> set[str]:
    base = f"/files/{doc_id}"
    return {
        f"{base}/original.pdf",
        f"{base}/normalized/searchable.pdf",
        f"{base}/content.md",
        f"{base}/manifest.json",
        f"{base}/thumbnails/thumb.webp",
    }


def _setup() -> tuple[InMemoryDocumentRepository, InMemoryFeatureRepository]:
    docs = InMemoryDocumentRepository()
    feats = InMemoryFeatureRepository()
    for did in ("good", "degraded", "lost"):
        docs.add(_doc(did))
        feats.seed_for_document(TENANT, did, [("thumbnail", 1), ("chunk_embed", 2)])
    return docs, feats


def test_ok_degraded_and_unrecoverable_are_classified() -> None:
    docs, feats = _setup()
    present = _all_artifacts("good") | _all_artifacts("degraded") | _all_artifacts("lost")
    present.discard("/files/degraded/thumbnails/thumb.webp")  # degraded: a derived artifact missing
    present.discard("/files/lost/original.pdf")  # lost: the irreplaceable original is gone

    report = repair_documents(
        document_repo=docs, feature_repo=feats, exists=present.__contains__, tenant_id=TENANT
    )

    assert report.checked == 3 and report.ok == 1
    assert report.repaired == ["degraded"]
    assert report.unrecoverable == ["lost"]
    # The degraded doc's features were re-queued to pending; the others untouched.
    assert all(f.status.value == "pending" for f in feats.list_for_document(TENANT, "degraded"))


def test_dry_run_reports_without_resetting() -> None:
    docs, feats = _setup()
    present = _all_artifacts("good") | _all_artifacts("degraded") | _all_artifacts("lost")
    present.discard("/files/degraded/content.md")  # degraded: a derived artifact missing
    present.discard("/files/lost/original.pdf")  # lost: original gone
    for f in feats.list_for_document(TENANT, "degraded"):
        feats.mark_done(f.id, feature_version=f.feature_version)

    report = repair_documents(
        document_repo=docs,
        feature_repo=feats,
        exists=present.__contains__,
        tenant_id=TENANT,
        dry_run=True,
    )

    assert report.repaired == ["degraded"] and report.unrecoverable == ["lost"]
    # dry-run must not change the ledger.
    assert all(f.status.value == "done" for f in feats.list_for_document(TENANT, "degraded"))
