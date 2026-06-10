"""Audit repository unit tests + pipeline emission tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.schemas import AuditEvent, AuditEventType
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.ingestion.inmemory import InMemoryIngestionJobRepository
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import DirectTextExtractor, PyMuPdfTextExtractor
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService

TENANT = "t1"


def _event(tenant: str, event_type: str, document_id: str | None) -> AuditEvent:
    return AuditEvent(
        id=f"{tenant}-{event_type}-{document_id}",
        tenant_id=tenant,
        event_type=event_type,
        actor="worker",
        document_id=document_id,
        timestamp=datetime.now(UTC),
    )


def test_inmemory_audit_is_tenant_scoped_and_filterable() -> None:
    repo = InMemoryAuditLogRepository()
    repo.record(_event("a", "document.received", "doc-1"))
    repo.record(_event("a", "document.activated", "doc-1"))
    repo.record(_event("b", "document.received", "doc-2"))

    assert len(repo.list_events("a")) == 2
    assert [e.event_type for e in repo.list_events("a", document_id="doc-1")] == [
        "document.activated",
        "document.received",
    ]  # newest first
    assert repo.list_events("a", document_id="doc-2") == []
    assert len(repo.list_events("b")) == 1


class FakeMime:
    def __init__(self, mime: str) -> None:
        self._mime = mime

    def detect(self, path: str) -> str:  # noqa: ARG002
        return self._mime


def _services(tmp_path: Path, mime: str, audit: InMemoryAuditLogRepository) -> IngestionServices:
    layout = FilesystemLayout(tmp_path, TENANT)
    layout.ensure()
    return IngestionServices(
        tenant_id=TENANT,
        job_repo=InMemoryIngestionJobRepository(),
        document_repo=InMemoryDocumentRepository(),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=FakeMime(mime),
        security_policy=DefaultSecurityPolicy(max_file_mb=10),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        audit_log=audit,
    )


def test_pipeline_emits_full_activity_for_a_text_document(tmp_path: Path) -> None:
    audit = InMemoryAuditLogRepository()
    services = _services(tmp_path, "text/plain", audit)
    (services.layout.ingest / "note.txt").write_bytes(b"hello")

    job = process_file(services, str(services.layout.ingest / "note.txt"))

    types = [e.event_type for e in audit.list_events(TENANT)]
    assert AuditEventType.DOCUMENT_RECEIVED.value in types
    assert AuditEventType.DOCUMENT_IDENTIFIED.value in types
    assert AuditEventType.DOCUMENT_ACTIVATED.value in types

    activated = next(
        e
        for e in audit.list_events(TENANT)
        if e.event_type == AuditEventType.DOCUMENT_ACTIVATED.value
    )
    assert activated.document_id == job.document_id
    assert "Parsed plain text" in activated.metadata["summary"]


def test_pipeline_emits_failed_for_unsupported(tmp_path: Path) -> None:
    audit = InMemoryAuditLogRepository()
    services = _services(tmp_path, "application/octet-stream", audit)
    (services.layout.ingest / "blob.bin").write_bytes(b"\x00\x01")

    process_file(services, str(services.layout.ingest / "blob.bin"))

    failed = [
        e for e in audit.list_events(TENANT) if e.event_type == AuditEventType.DOCUMENT_FAILED.value
    ]
    assert failed and failed[0].metadata["error_code"] == "unsupported_type"
