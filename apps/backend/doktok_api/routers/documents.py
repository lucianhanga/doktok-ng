"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

import base64
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from doktok_contracts.ports import (
    AuditLogRepository,
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
    DocumentContentMeta,
    DocumentDetail,
    DocumentEntity,
    DocumentEntitySummary,
    DocumentFeature,
    DocumentListPage,
    DocumentStatus,
    EntityTypeCount,
)
from doktok_core.documents.artifacts import THUMBNAIL_REL
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from doktok_api.dependencies import (
    Tenant,
    get_audit_repository,
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
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]

# Bytes of extracted text returned inline on the detail card; the full text is fetched lazily.
_EXCERPT_CHARS = 4000
# Highest-frequency entities surfaced on the card; the full list is fetched lazily.
_TOP_ENTITIES = 12


def _encode_cursor(anchor: tuple[datetime, str] | None) -> str | None:
    if anchor is None:
        return None
    raw = f"{anchor[0].isoformat()}|{anchor[1]}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(token: str | None) -> tuple[datetime, str] | None:
    if not token:
        return None
    try:
        ts, doc_id = base64.urlsafe_b64decode(token).decode().split("|", 1)
        return datetime.fromisoformat(ts), doc_id
    except (ValueError, TypeError) as exc:  # malformed/stale token is client input, not a 500
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


@router.get("", response_model=DocumentListPage)
def list_documents(
    tenant: Tenant,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    status: Annotated[DocumentStatus | None, Query()] = None,
    needs_attention: Annotated[bool, Query()] = False,
) -> DocumentListPage:
    items, total, next_anchor = repo.list_documents(
        tenant.tenant_id,
        limit=limit,
        cursor=_decode_cursor(cursor),
        status=status,
        category=category,
        needs_attention=needs_attention,
    )
    return DocumentListPage(items=items, total=total, next_cursor=_encode_cursor(next_anchor))


@router.get("/{document_id}", response_model=Document)
def get_document(document_id: str, tenant: Tenant, repo: Repo) -> Document:
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@router.get("/{document_id}/detail", response_model=DocumentDetail)
def get_document_detail(
    document_id: str,
    tenant: Tenant,
    repo: Repo,
    features: Features,
    categories: Categories,
    entities: Entities,
    audit: Audit,
) -> DocumentDetail:
    """One aggregate for the detail card: document, processing, categories, an entity summary, a
    content excerpt, and recent activity. The full text and full entity list are fetched lazily via
    /content and /entities when their tab is opened."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    all_entities = entities.list_for_document(tenant.tenant_id, document_id)
    by_type = Counter(e.entity_type.value for e in all_entities)
    entity_summary = DocumentEntitySummary(
        total=len(all_entities),
        by_type=[EntityTypeCount(entity_type=t, count=n) for t, n in by_type.most_common()],
        top=sorted(all_entities, key=lambda e: e.frequency, reverse=True)[:_TOP_ENTITIES],
    )

    text = ""
    if document.storage_path:
        path = Path(document.storage_path) / "content.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
    content = DocumentContentMeta(length=len(text), excerpt=text[:_EXCERPT_CHARS])

    return DocumentDetail(
        document=document,
        features=features.list_for_document(tenant.tenant_id, document_id),
        categories=categories.list_for_document(tenant.tenant_id, document_id),
        entities=entity_summary,
        content=content,
        recent_activity=audit.list_events(tenant.tenant_id, document_id=document_id, limit=10),
    )


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


def _document_dir(document: Document, files_root: Path) -> Path | None:
    """The document's on-disk folder, guarded against escaping the tenant files root."""
    if not document.storage_path:
        return None
    base = Path(document.storage_path).resolve()
    root = files_root.resolve()
    return base if (root == base or root in base.parents) else None


@router.post("/{document_id}/reingest")
def reingest_document(
    document_id: str, request: Request, tenant: Tenant, repo: Repo, jobs: Jobs
) -> dict[str, str]:
    """Re-ingest a document of any status: read its original file, purge its DB rows and files, and
    drop the original back in the ingest folder so the worker reprocesses it cleanly."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    files_root = Path(request.app.state.settings.files_root)
    base = _document_dir(document, files_root)
    if base is None:
        raise HTTPException(status_code=404, detail="document files not found")

    rel = document.metadata.get("original") or document.original_filename
    source = (base / str(rel)).resolve()
    if (source != base and base not in source.parents) or not source.is_file():
        raise HTTPException(status_code=404, detail="original file not found")
    data = source.read_bytes()  # read before purging, so nothing is lost

    if document.sha256:
        jobs.delete_for_sha(tenant.tenant_id, document.sha256)
    repo.delete(tenant.tenant_id, document_id)  # FK-cascades chunks/entities/features/links/records
    shutil.rmtree(base, ignore_errors=True)

    ingest_dir = files_root / tenant.tenant_id / "ingest"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    # Defense-in-depth: re-basename the stored filename so a crafted value can't escape ingest/.
    safe_name = Path(document.original_filename).name or "document"
    (ingest_dir / safe_name).write_bytes(data)
    return {"status": "queued", "filename": safe_name}


@router.delete("/{document_id}")
def delete_document(
    document_id: str, request: Request, tenant: Tenant, repo: Repo, jobs: Jobs
) -> dict[str, str]:
    """Delete a document, its files, and all its derived rows (chunks/entities/features/links/
    records via FK cascade)."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        # Idempotent: a retried DELETE of an already-removed document is a success, not a 404.
        return {"status": "deleted"}

    base = _document_dir(document, Path(request.app.state.settings.files_root))
    if document.sha256:
        jobs.delete_for_sha(tenant.tenant_id, document.sha256)
    repo.delete(tenant.tenant_id, document_id)  # FK-cascades derived rows
    # Remove files last: with DB rows already gone, a failed rmtree leaves only orphan files (which
    # a retry clears), never a dangling DB row pointing at missing files.
    if base is not None:
        shutil.rmtree(base, ignore_errors=True)
    return {"status": "deleted"}


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


@router.get("/{document_id}/thumbnail")
def get_document_thumbnail(
    document_id: str,
    tenant: Tenant,
    repo: Repo,
) -> FileResponse:
    """Serve the document's first-page preview (WebP) for the card and grid/list views.

    Returns 404 until the ``thumbnail`` feature has produced it (or for documents that cannot be
    rendered), so the UI falls back to a placeholder.
    """
    document = repo.get(tenant.tenant_id, document_id)
    if document is None or not document.storage_path:
        raise HTTPException(status_code=404, detail="document not found")
    base = Path(document.storage_path)
    path = (base / THUMBNAIL_REL).resolve()
    if base.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="thumbnail not available")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "X-Content-Type-Options": "nosniff",
            # Bytes are fixed for a document version; allow a day of private caching.
            "Cache-Control": "private, max-age=86400",
        },
    )
