"""Ingestion pipeline orchestration (M1 + M2).

Coordinates the full lifecycle of a dropped file using ports only (ADR-0001, ADR-0004, ADR-0007):

    move to in.process/{job_id}/source -> hash -> detect MIME -> dedup -> security decision
      -> extract -> write canonical artifacts -> create active document

Outcomes:
- born-digital text/markdown/PDF -> ``active`` document under docs.active/{document_id}/
- needs OCR (images, scanned PDF) -> job ``failed`` (``needs_ocr``), pending M3
- duplicate (same sha256, per tenant) -> job ``failed`` (``duplicate_hash``)
- disallowed/too large -> job ``failed`` (``unsupported_type`` / ``too_large``)
- dangerous type -> job ``quarantined``

Jobs are tagged with the tenant from ``IngestionServices`` (ADR-0007).
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from doktok_contracts.ports import (
    AuditLogRepository,
    DocumentRepository,
    FileStorage,
    HashService,
    IngestionJobRepository,
    MimeDetector,
    OcrExtractor,
    PdfClassifier,
    PdfRenderer,
    PdfTextExtractor,
    QuarantineService,
    SearchablePdfBuilder,
    SecurityPolicy,
    TextExtractor,
)
from doktok_contracts.schemas import (
    AuditEventType,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    SecurityDecision,
)

from doktok_core.audit.logger import record_activity
from doktok_core.documents.artifacts import write_document_artifacts
from doktok_core.extraction.service import NeedsOcrError, extract_document
from doktok_core.ingestion.layout import FilesystemLayout

DETECTOR_NAME = "libmagic"

_ACTIVATION_SUMMARY = {
    "text": "Parsed plain text",
    "markdown": "Parsed Markdown",
    "pdf_text": "Extracted embedded PDF text",
    "ocr": "OCR'd page images; searchable PDF created",
    "pdf_mixed": "Mixed PDF: embedded text kept, scanned pages OCR'd",
}


def _activation_summary(method: str, page_count: int) -> str:
    base = _ACTIVATION_SUMMARY.get(method, f"Extracted ({method})")
    return f"{base} ({page_count} page(s))"


@dataclass
class IngestionServices:
    """Ports + layout the pipeline depends on (wired per tenant at the composition root)."""

    tenant_id: str
    job_repo: IngestionJobRepository
    document_repo: DocumentRepository
    file_storage: FileStorage
    hash_service: HashService
    mime_detector: MimeDetector
    security_policy: SecurityPolicy
    quarantine_service: QuarantineService
    text_extractor: TextExtractor
    pdf_extractor: PdfTextExtractor
    layout: FilesystemLayout
    # OCR services (M3). When absent, files needing OCR fail with ``needs_ocr``.
    ocr_extractor: OcrExtractor | None = None
    pdf_renderer: PdfRenderer | None = None
    searchable_pdf_builder: SearchablePdfBuilder | None = None
    pdf_classifier: PdfClassifier | None = None
    # Page image-coverage at/above which a PDF page is treated as scanned and re-OCR'd.
    ocr_image_coverage: float = 1.0
    # Activity/audit trail (M3.6). When absent, no audit events are recorded.
    audit_log: AuditLogRepository | None = None


def _audit(
    services: IngestionServices,
    event_type: AuditEventType,
    job: IngestionJob,
    *,
    document_id: str | None = None,
    **details: object,
) -> None:
    if services.audit_log is not None:
        record_activity(
            services.audit_log,
            services.tenant_id,
            event_type,
            document_id=document_id,
            job_id=job.id,
            details=details,
        )


def _new_id() -> str:
    return uuid.uuid4().hex


def process_file(services: IngestionServices, source_path: str) -> IngestionJob:
    """Run the full ingestion for a single stable file. Returns the resulting job."""
    job_id = _new_id()
    now = datetime.now(UTC)
    original_path = str(source_path)
    job = IngestionJob(
        id=job_id,
        tenant_id=services.tenant_id,
        source_path=original_path,
        status=JobStatus.QUEUED,
        started_at=now,
        metadata={"original_ingest_path": original_path},
    )
    services.job_repo.add(job)
    _audit(
        services,
        AuditEventType.DOCUMENT_RECEIVED,
        job,
        filename=Path(original_path).name,
        source=original_path,
    )

    workdir = services.layout.job_workdir(job_id)
    try:
        dest = services.layout.job_source(job_id)
        job.status = JobStatus.DETECTING
        services.file_storage.move(original_path, str(dest))
        job.source_path = str(dest)

        job.status = JobStatus.HASHING
        job.sha256 = services.hash_service.sha256(str(dest))
        job.detected_mime = services.mime_detector.detect(str(dest))
        services.job_repo.update(job)
        _audit(
            services,
            AuditEventType.DOCUMENT_IDENTIFIED,
            job,
            mime=job.detected_mime,
            sha256=job.sha256,
        )

        if _is_duplicate(services, job):
            return _fail(
                services,
                job,
                workdir,
                code="duplicate_hash",
                message=f"content with sha256 {job.sha256} has already been ingested",
            )

        size_bytes = os.path.getsize(dest)
        decision = services.security_policy.decide(job.detected_mime, size_bytes)
        if decision is SecurityDecision.QUARANTINE:
            return _quarantine(services, job, workdir)
        if decision is SecurityDecision.REJECT:
            max_bytes = getattr(services.security_policy, "max_file_bytes", None)
            too_large = max_bytes is not None and size_bytes > max_bytes
            return _fail(
                services,
                job,
                workdir,
                code="too_large" if too_large else "unsupported_type",
                message=f"rejected mime={job.detected_mime} size={size_bytes}",
            )

        return _activate(services, job, workdir)
    except Exception as exc:  # noqa: BLE001 - record any failure on the job, do not crash the worker
        return _fail(services, job, workdir, code="internal_error", message=str(exc))


def _activate(services: IngestionServices, job: IngestionJob, workdir: Path) -> IngestionJob:
    """Extract content, write canonical artifacts, and create an active document (M2)."""
    job.status = JobStatus.EXTRACTING
    services.job_repo.update(job)
    try:
        result, normalized_pdf = extract_document(
            job.detected_mime or "",
            job.source_path,
            text_extractor=services.text_extractor,
            pdf_extractor=services.pdf_extractor,
            ocr=services.ocr_extractor,
            renderer=services.pdf_renderer,
            builder=services.searchable_pdf_builder,
            classifier=services.pdf_classifier,
            ocr_image_coverage=services.ocr_image_coverage,
        )
    except NeedsOcrError as exc:
        return _fail(services, job, workdir, code="needs_ocr", message=str(exc))

    document_id = _new_id()
    original_filename = Path(job.metadata.get("original_ingest_path", job.source_path)).name

    job.status = JobStatus.ACTIVATING
    artifacts = write_document_artifacts(
        services.file_storage,
        services.layout,
        document_id,
        tenant_id=services.tenant_id,
        original_source_path=job.source_path,
        original_filename=original_filename,
        sha256=job.sha256 or "",
        detected_mime=job.detected_mime,
        detector=DETECTOR_NAME,
        result=result,
        normalized_pdf=normalized_pdf,
    )

    now = datetime.now(UTC)
    document = Document(
        id=document_id,
        tenant_id=services.tenant_id,
        sha256=job.sha256 or "",
        original_filename=original_filename,
        detected_mime=job.detected_mime,
        title=Path(original_filename).stem or original_filename,
        status=DocumentStatus.ACTIVE,
        storage_path=artifacts.storage_path,
        created_at=now,
        activated_at=now,
        metadata={
            "extraction_method": result.extraction_method,
            "page_count": result.page_count,
            "ocr_confidence": result.ocr_confidence,
            "original": artifacts.original,
            "system_document": artifacts.system_document,
        },
    )
    services.document_repo.add(document)

    job.document_id = document_id
    job.status = JobStatus.ACTIVE
    job.finished_at = now
    # The source file has been moved into docs.active/; drop the now-empty working dir.
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    services.job_repo.update(job)
    _audit(
        services,
        AuditEventType.DOCUMENT_ACTIVATED,
        job,
        document_id=document_id,
        filename=original_filename,
        extraction_method=result.extraction_method,
        page_count=result.page_count,
        ocr_confidence=result.ocr_confidence,
        system_document=artifacts.system_document,
        summary=_activation_summary(result.extraction_method, result.page_count),
    )
    return job


def _is_duplicate(services: IngestionServices, job: IngestionJob) -> bool:
    if not job.sha256:
        return False
    for other in services.job_repo.find_by_sha256(services.tenant_id, job.sha256):
        if other.id == job.id:
            continue
        if other.status not in (JobStatus.FAILED, JobStatus.QUARANTINED):
            return True
    return False


def _fail(
    services: IngestionServices,
    job: IngestionJob,
    workdir: Path,
    *,
    code: str,
    message: str,
) -> IngestionJob:
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.finished_at = datetime.now(UTC)
    if workdir.exists():
        services.file_storage.move(str(workdir), str(services.layout.failed_dir(job.id)))
    services.job_repo.update(job)
    _audit(
        services,
        AuditEventType.DOCUMENT_FAILED,
        job,
        document_id=job.document_id,
        error_code=code,
        error_message=message,
        mime=job.detected_mime,
    )
    return job


def _quarantine(services: IngestionServices, job: IngestionJob, workdir: Path) -> IngestionJob:
    job.status = JobStatus.QUARANTINED
    job.error_code = "quarantined"
    job.error_message = f"disallowed content type {job.detected_mime}"
    job.finished_at = datetime.now(UTC)
    if workdir.exists():
        services.quarantine_service.quarantine(str(workdir), reason=job.error_message)
    services.job_repo.update(job)
    _audit(
        services,
        AuditEventType.DOCUMENT_QUARANTINED,
        job,
        mime=job.detected_mime,
        reason=job.error_message,
    )
    return job
