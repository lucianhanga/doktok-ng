"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Literal

from doktok_contracts.ports import (
    CategoryRepository,
    DocumentRepository,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
)
from doktok_contracts.schemas import (
    Category,
    Document,
    DocumentContent,
    DocumentEntity,
    DocumentFeature,
    DocumentStatus,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from doktok_api.dependencies import (
    Tenant,
    get_category_repository,
    get_document_repository,
    get_entity_repository,
    get_feature_repository,
    get_job_repository,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

Repo = Annotated[DocumentRepository, Depends(get_document_repository)]
Entities = Annotated[EntityRepository, Depends(get_entity_repository)]
Features = Annotated[FeatureRepository, Depends(get_feature_repository)]
Categories = Annotated[CategoryRepository, Depends(get_category_repository)]
Jobs = Annotated[IngestionJobRepository, Depends(get_job_repository)]


@router.get("", response_model=list[Document])
def list_documents(
    tenant: Tenant,
    repo: Repo,
    categories: Categories,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    category: Annotated[str | None, Query()] = None,
) -> list[Document]:
    if category:
        return categories.documents_for_category(
            tenant.tenant_id, category, limit=limit, offset=offset
        )
    return repo.list_documents(tenant.tenant_id, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=Document)
def get_document(document_id: str, tenant: Tenant, repo: Repo) -> Document:
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.get("/{document_id}/content", response_model=DocumentContent)
def get_document_content(document_id: str, tenant: Tenant, repo: Repo) -> DocumentContent:
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    content = ""
    if document.storage_path:
        path = Path(document.storage_path) / "content.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
    return DocumentContent(document_id=document_id, content=content)


@router.get("/{document_id}/entities", response_model=list[DocumentEntity])
def get_document_entities(
    document_id: str, tenant: Tenant, entities: Entities
) -> list[DocumentEntity]:
    return entities.list_for_document(tenant.tenant_id, document_id)


@router.get("/{document_id}/features", response_model=list[DocumentFeature])
def get_document_features(
    document_id: str, tenant: Tenant, features: Features
) -> list[DocumentFeature]:
    return features.list_for_document(tenant.tenant_id, document_id)


@router.get("/{document_id}/categories", response_model=list[Category])
def get_document_categories(
    document_id: str, tenant: Tenant, categories: Categories
) -> list[Category]:
    return categories.list_for_document(tenant.tenant_id, document_id)


@router.post("/{document_id}/features/{feature}/retry")
def retry_document_feature(
    document_id: str, feature: str, tenant: Tenant, features: Features
) -> dict[str, str]:
    if not features.reset(tenant.tenant_id, document_id, feature):
        raise HTTPException(status_code=404, detail="feature not found for this document")
    return {"status": "queued"}


@router.post("/{document_id}/reingest")
def reingest_document(
    document_id: str, request: Request, tenant: Tenant, repo: Repo, jobs: Jobs
) -> dict[str, str]:
    """Re-queue a FAILED document: move its original back to the ingest folder and remove the failed
    records, so the worker reprocesses it cleanly on its next run."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None or not document.storage_path:
        raise HTTPException(status_code=404, detail="document not found")
    if document.status != DocumentStatus.FAILED:
        raise HTTPException(status_code=400, detail="only failed documents can be re-ingested")

    base = Path(document.storage_path)
    rel = document.metadata.get("original") or document.original_filename
    source = (base / str(rel)).resolve()
    base_resolved = base.resolve()
    if (source != base_resolved and base_resolved not in source.parents) or not source.is_file():
        raise HTTPException(status_code=404, detail="original file not found")

    ingest_dir = Path(request.app.state.settings.files_root) / tenant.tenant_id / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(ingest_dir / document.original_filename))

    if document.sha256:
        jobs.delete_failed_for_sha(tenant.tenant_id, document.sha256)
    repo.delete(tenant.tenant_id, document_id)
    shutil.rmtree(base, ignore_errors=True)
    return {"status": "queued", "filename": document.original_filename}


def _resolve_file(document: Document, variant: str) -> tuple[Path, str]:
    """Resolve a document's on-disk file + media type for the variant (with a traversal guard)."""
    base = Path(document.storage_path or "")
    if variant == "normalized":
        rel = document.metadata.get("system_document")
        if not rel:
            raise HTTPException(status_code=404, detail="normalized file not available")
        path = base / str(rel)
        media_type = "application/pdf" if path.suffix == ".pdf" else (document.detected_mime or "")
    else:
        # active docs store the canonical name in metadata; failed/duplicate keep the original name.
        rel = document.metadata.get("original") or document.original_filename
        path = base / str(rel)
        media_type = document.detected_mime or ""
    resolved, base_resolved = path.resolve(), base.resolve()
    if resolved != base_resolved and base_resolved not in resolved.parents:
        raise HTTPException(status_code=404, detail="file not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return path, media_type or "application/octet-stream"


@router.get("/{document_id}/file")
def get_document_file(
    document_id: str,
    tenant: Tenant,
    repo: Repo,
    variant: Annotated[Literal["original", "normalized"], Query()] = "original",
    disposition: Annotated[Literal["inline", "attachment"], Query()] = "inline",
) -> FileResponse:
    """Serve the raw document bytes (for in-browser preview / open-in-new-tab / download)."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None or not document.storage_path:
        raise HTTPException(status_code=404, detail="document not found")
    path, media_type = _resolve_file(document, variant)
    return FileResponse(
        path,
        media_type=media_type,
        filename=document.original_filename,
        content_disposition_type=disposition,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"},
    )
