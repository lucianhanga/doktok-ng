"""Ingestion job endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from doktok_contracts.ports import IngestionJobRepository
from doktok_contracts.schemas import AuditEventType, IngestionJob, IngestUploadResult
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.ingestion.layout import FilesystemLayout
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from doktok_api.dependencies import Tenant, get_audit_repository, get_job_repository

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])

Repo = Annotated[IngestionJobRepository, Depends(get_job_repository)]

# Bounded chunk size for the streaming upload write (F-12); mirrors the restore preview's pattern.
_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


def _safe_filename(name: str | None) -> str | None:
    """Reduce an uploaded name to a safe basename, or None if it is unusable / path-traversal."""
    base = Path(name or "").name.strip()
    if not base or base in (".", "..") or "/" in base or "\\" in base:
        return None
    return base


def _unique_target(ingest: Path, name: str) -> Path:
    """A non-colliding path in the ingest folder (suffixes the name if it already exists)."""
    target = ingest / name
    if not target.exists():
        return target
    p = Path(name)
    return ingest / f"{p.stem}-{uuid.uuid4().hex[:8]}{p.suffix}"


@router.post("/upload", response_model=IngestUploadResult)
async def upload_documents(
    tenant: Tenant,
    request: Request,
    files: Annotated[list[UploadFile], File()],
) -> IngestUploadResult:
    """Accept dropped documents and write them into the tenant's ingest folder for the worker (M14
    #370). Each file is written to a hidden temp name then renamed - the worker ignores dotfiles, so
    it never claims a partial upload. Accepts any type; the pipeline sorts out the rest.

    The copy is STREAMED in bounded chunks (F-12, mirroring the restore preview): a read-then-check
    approach made the whole file RAM-resident before the size cap fired, so the cap is now enforced
    while writing - abort + cleanup as soon as it is crossed."""
    settings = request.app.state.settings
    # Too many files is a batch-level error: refuse the WHOLE drop (you can't pick which to keep),
    # unlike an individual oversized file below, which is rejected on its own so the rest still go.
    if len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=400,
            detail=(
                f"at most {settings.max_upload_files} files per upload; you sent {len(files)}. "
                "Please split into smaller batches."
            ),
        )
    layout = FilesystemLayout(settings.files_root, tenant.tenant_id)
    layout.ensure()
    limit = settings.max_request_mb * 1024 * 1024
    accepted: list[str] = []
    rejected: list[str] = []
    for upload in files:
        safe = _safe_filename(upload.filename)
        if safe is None:
            rejected.append(f"{upload.filename!r}: invalid filename")
            continue
        target = _unique_target(layout.ingest, safe)
        tmp = layout.ingest / f".upload-{uuid.uuid4().hex}.part"
        written = 0
        oversized = False
        try:
            with tmp.open("wb") as out:
                while chunk := await upload.read(_UPLOAD_CHUNK):
                    written += len(chunk)
                    if written > limit:
                        oversized = True
                        break
                    out.write(chunk)
        finally:
            await upload.close()
        if oversized:
            tmp.unlink(missing_ok=True)
            rejected.append(f"{safe}: exceeds {settings.max_request_mb} MB")
            continue
        if written == 0:
            tmp.unlink(missing_ok=True)
            rejected.append(f"{safe}: empty file")
            continue
        tmp.rename(target)  # atomic publish; the worker only claims non-dotfiles
        accepted.append(target.name)
        # F-21 (#635): record the HUMAN uploader - the worker's pipeline events carry
        # actor="worker", so without this row "who uploaded this file?" is unanswerable.
        record_activity(
            get_audit_repository(request),
            tenant.tenant_id,
            AuditEventType.DOCUMENT_UPLOADED,
            actor=actor_identity(tenant),
            actor_kind="user",
            doc_filename=target.name,
        )
    return IngestUploadResult(accepted=accepted, rejected=rejected)


@router.get("/jobs", response_model=list[IngestionJob])
def list_jobs(
    tenant: Tenant,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IngestionJob]:
    return repo.list_jobs(tenant.tenant_id, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=IngestionJob)
def get_job(job_id: str, tenant: Tenant, repo: Repo) -> IngestionJob:
    job = repo.get(tenant.tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
