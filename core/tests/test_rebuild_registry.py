"""Rebuild the document registry from files_root manifests (files-ahead-of-DB recovery, #749)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.documents.rebuild_registry import ManifestLoader, rebuild_registry
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.features.processors import ENRICHMENT_STAGES

TENANT = "t"


def _manifest(doc_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "document_id": doc_id,
        "tenant_id": TENANT,
        "sha256": (doc_id + "a" * 64)[:64],
        "original_filename": f"{doc_id}.pdf",
        "detected_mime": "application/pdf",
        "created_at": "2026-07-24T08:49:00+00:00",
        "extraction_method": "ocr",
        "page_count": 2,
        "language": "de",
        "system_document": "normalized/searchable.pdf",
        "artifacts": {"original": "original.pdf"},
    }
    base.update(overrides)
    return base


def _loader(entries: list[tuple[str, dict[str, Any] | None]]) -> ManifestLoader:
    return lambda tenant_id: entries


def test_registers_active_document_and_requeues_enrichment_only() -> None:
    docs = InMemoryDocumentRepository()
    feats = InMemoryFeatureRepository()
    entries: list[tuple[str, dict[str, Any] | None]] = [
        ("/files/t/docs.active/doc1", _manifest("doc1"))
    ]

    report = rebuild_registry(
        document_repo=docs,
        feature_repo=feats,
        load_manifests=_loader(entries),
        tenant_id=TENANT,
        stages=ENRICHMENT_STAGES,
    )

    assert report.registered == ["doc1"] and report.scanned == 1
    doc = docs.get(TENANT, "doc1")
    assert doc is not None
    assert doc.status is DocumentStatus.ACTIVE
    assert doc.storage_path == "/files/t/docs.active/doc1"
    assert doc.sha256 == _manifest("doc1")["sha256"]
    assert doc.activated_at is not None and doc.ingested_at is not None
    assert doc.metadata["original"] == "original.pdf"
    assert doc.metadata["system_document"] == "normalized/searchable.pdf"
    assert doc.metadata["page_count"] == 2
    assert doc.metadata["registry_rebuilt"] is True

    rows = {f.feature: f for f in feats.list_for_document(TENANT, "doc1")}
    # extract is recorded DONE (artifacts on disk -> no OCR re-run); every enrichment stage pending.
    assert rows["extract"].status == "done"
    for name, _version in ENRICHMENT_STAGES:
        assert rows[name].status == "pending"
    assert "extract" not in [name for name, _ in ENRICHMENT_STAGES]


def test_existing_ids_are_skipped_and_run_is_idempotent() -> None:
    docs = InMemoryDocumentRepository()
    feats = InMemoryFeatureRepository()
    docs.add(
        Document(
            id="doc1",
            tenant_id=TENANT,
            sha256="x" * 64,
            original_filename="doc1.pdf",
            status=DocumentStatus.ACTIVE,
            created_at=datetime.now(UTC),
        )
    )
    entries: list[tuple[str, dict[str, Any] | None]] = [
        ("/files/t/docs.active/doc1", _manifest("doc1"))
    ]

    report = rebuild_registry(
        document_repo=docs,
        feature_repo=feats,
        load_manifests=_loader(entries),
        tenant_id=TENANT,
        stages=ENRICHMENT_STAGES,
    )

    assert report.registered == [] and report.skipped_existing == ["doc1"]
    assert feats.list_for_document(TENANT, "doc1") == []  # untouched
    doc = docs.get(TENANT, "doc1")
    assert doc is not None
    assert doc.sha256 == "x" * 64  # the row was not overwritten


def test_dirs_without_a_valid_manifest_are_reported_as_orphans() -> None:
    docs = InMemoryDocumentRepository()
    feats = InMemoryFeatureRepository()
    entries: list[tuple[str, dict[str, Any] | None]] = [
        ("/files/t/docs.active/broken", None),
        ("/files/t/docs.active/doc1", _manifest("doc1")),
    ]

    report = rebuild_registry(
        document_repo=docs,
        feature_repo=feats,
        load_manifests=_loader(entries),
        tenant_id=TENANT,
        stages=ENRICHMENT_STAGES,
    )

    assert report.orphans == ["/files/t/docs.active/broken"]
    assert report.registered == ["doc1"]


def test_dry_run_changes_nothing() -> None:
    docs = InMemoryDocumentRepository()
    feats = InMemoryFeatureRepository()
    entries: list[tuple[str, dict[str, Any] | None]] = [
        ("/files/t/docs.active/doc1", _manifest("doc1"))
    ]

    report = rebuild_registry(
        document_repo=docs,
        feature_repo=feats,
        load_manifests=_loader(entries),
        tenant_id=TENANT,
        stages=ENRICHMENT_STAGES,
        dry_run=True,
    )

    assert report.registered == ["doc1"]  # reported...
    assert docs.get(TENANT, "doc1") is None  # ...but nothing was written
    assert feats.list_for_document(TENANT, "doc1") == []


def test_enrichment_stages_cover_every_feature_processor() -> None:
    """Drift guard: a new *Feature processor must be added to ENRICHMENT_STAGES, or recovery
    tooling silently stops re-queuing it."""
    import inspect

    from doktok_core.features import processors

    declared = {name for name, _ in ENRICHMENT_STAGES}
    discovered = {
        obj.name
        for _cls_name, obj in inspect.getmembers(processors, inspect.isclass)
        if _cls_name.endswith("Feature")
        and isinstance(getattr(obj, "name", None), str)
        and obj.__module__ == processors.__name__
    }
    assert declared == discovered
