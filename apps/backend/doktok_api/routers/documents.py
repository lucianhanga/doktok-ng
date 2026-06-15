"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

import base64
import json
import shutil
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote

from doktok_contracts.ports import (
    AuditLogRepository,
    CategoryRepository,
    DocumentRepository,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
)
from doktok_contracts.schemas import (
    AuditEventType,
    Category,
    Document,
    DocumentContent,
    DocumentContentMeta,
    DocumentDetail,
    DocumentEntity,
    DocumentEntitySummary,
    DocumentFeature,
    DocumentIdSelection,
    DocumentLayout,
    DocumentListPage,
    DocumentSort,
    DocumentStatus,
    EntityType,
    EntityTypeCount,
    LayoutLine,
    LayoutPage,
    ListAnchor,
    SortDir,
    TokenMatch,
)
from doktok_core.audit.logger import record_activity
from doktok_core.documents.artifacts import NORMALIZED_PDF_REL, THUMBNAIL_REL
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

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


_MAX_TOKENS = 20  # cap on token filters per request


def _encode_cursor(anchor: ListAnchor | None) -> str | None:
    if anchor is None:
        return None
    value = anchor.value
    if value is None:
        payload = "n:"
    elif isinstance(value, datetime):
        payload = "t:" + value.isoformat()
    elif isinstance(value, date):
        payload = "d:" + value.isoformat()
    else:
        payload = "s:" + quote(str(value), safe="")  # percent-encode so '|' can't break the split
    raw = f"v2|{anchor.sort.value}|{anchor.direction.value}|{payload}|{anchor.doc_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(token: str | None, sort: DocumentSort, direction: SortDir) -> ListAnchor | None:
    """Decode an opaque cursor and verify it was produced for the requested sort/direction; a
    malformed, stale (v1), or mismatched cursor is client input -> 400, never a 500."""
    if not token:
        return None
    try:
        ver, csort, cdir, payload, doc_id = base64.urlsafe_b64decode(token).decode().split("|")
        if ver != "v2":
            raise ValueError("stale cursor")
        if csort != sort.value or cdir != direction.value:
            raise ValueError("cursor does not match the requested ordering")
        tag, _, body = payload.partition(":")
        value: datetime | date | str | None
        if tag == "n":
            value = None
        elif tag == "t":
            value = datetime.fromisoformat(body)
        elif tag == "d":
            value = date.fromisoformat(body)
        elif tag == "s":
            value = unquote(body)
        else:
            raise ValueError("bad cursor value")
        return ListAnchor(sort=sort, direction=direction, value=value, doc_id=doc_id)
    except (ValueError, TypeError) as exc:
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
    unidentifiable: Annotated[bool | None, Query()] = None,
    sort: Annotated[DocumentSort, Query()] = DocumentSort.ACQUIRED,
    direction: Annotated[SortDir, Query(alias="dir")] = SortDir.DESC,
    token: Annotated[list[str] | None, Query()] = None,
    token_type: Annotated[EntityType | None, Query()] = None,
    token_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
) -> DocumentListPage:
    tokens = _validated_tokens(token)
    items, total, next_anchor = repo.list_documents(
        tenant.tenant_id,
        limit=limit,
        cursor=_decode_cursor(cursor, sort, direction),
        status=status,
        category=category,
        needs_attention=needs_attention,
        unidentifiable=unidentifiable,
        sort=sort,
        direction=direction,
        tokens=tokens,
        token_type=token_type,
        token_match=token_match,
    )
    return DocumentListPage(items=items, total=total, next_cursor=_encode_cursor(next_anchor))


def _validated_tokens(token: list[str] | None) -> tuple[str, ...]:
    tokens = tuple(t for t in (token or []) if t.strip())
    if len(tokens) > _MAX_TOKENS:
        raise HTTPException(status_code=400, detail=f"at most {_MAX_TOKENS} tokens")
    return tokens


@router.get("/ids", response_model=DocumentIdSelection)
def list_document_ids(
    tenant: Tenant,
    repo: Repo,
    category: Annotated[str | None, Query()] = None,
    status: Annotated[DocumentStatus | None, Query()] = None,
    needs_attention: Annotated[bool, Query()] = False,
    unidentifiable: Annotated[bool | None, Query()] = None,
    token: Annotated[list[str] | None, Query()] = None,
    token_type: Annotated[EntityType | None, Query()] = None,
    token_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
) -> DocumentIdSelection:
    """All document ids matching the filters (for 'select all matching' bulk actions)."""
    ids, total, truncated = repo.list_document_ids(
        tenant.tenant_id,
        status=status,
        category=category,
        needs_attention=needs_attention,
        unidentifiable=unidentifiable,
        tokens=_validated_tokens(token),
        token_type=token_type,
        token_match=token_match,
    )
    return DocumentIdSelection(ids=ids, total=total, truncated=truncated)


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


@router.get("/{document_id}/layout", response_model=DocumentLayout)
def get_document_layout(document_id: str, tenant: Tenant, repo: Repo) -> DocumentLayout:
    """Per-page OCR line boxes (from content.json) for overlaying on the page image. Empty for
    documents OCR'd before box persistence (#285) or never OCR'd."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    pages: list[LayoutPage] = []
    if document.storage_path:
        path = Path(document.storage_path) / "content.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for page in data.get("pages", []):
                lines = page.get("lines")
                if not lines:
                    continue  # only pages with OCR geometry
                pages.append(
                    LayoutPage(
                        page_number=page["page_number"],
                        width_px=page.get("width_px", 0),
                        height_px=page.get("height_px", 0),
                        dpi=page.get("render_dpi"),
                        lines=[
                            LayoutLine(text=ln["text"], x0=b[0], y0=b[1], x1=b[2], y1=b[3])
                            for ln in lines
                            if (b := ln.get("bbox")) and len(b) == 4
                        ],
                    )
                )
    return DocumentLayout(document_id=document_id, pages=pages)


@router.get("/{document_id}/page/{page_number}/image")
def get_document_page_image(
    document_id: str,
    page_number: int,
    tenant: Tenant,
    repo: Repo,
    dpi: Annotated[int, Query(ge=72, le=300)] = 150,
) -> Response:
    """Render one page (1-based) of the normalized/searchable PDF to PNG, for the box-overlay
    viewer. The page is sized to the OCR image, so the layout boxes overlay it by proportion."""
    import fitz

    document = repo.get(tenant.tenant_id, document_id)
    if document is None or not document.storage_path:
        raise HTTPException(status_code=404, detail="document not found")
    pdf_path = (Path(document.storage_path) / NORMALIZED_PDF_REL).resolve()
    base = Path(document.storage_path).resolve()
    if base not in pdf_path.parents or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="page image not available")
    with fitz.open(pdf_path) as doc:
        if page_number < 1 or page_number > doc.page_count:
            raise HTTPException(status_code=404, detail="page out of range")
        pix = doc[page_number - 1].get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0))
        png = bytes(pix.tobytes("png"))
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


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
    document_id: str, feature: str, tenant: Tenant, features: Features, audit: Audit
) -> dict[str, str]:
    if not features.reset(tenant.tenant_id, document_id, feature):
        raise HTTPException(status_code=404, detail="feature not found for this document")
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.FEATURE_RETRIED,
        actor="user",
        actor_kind="user",
        document_id=document_id,
        description=f"{feature} re-queued by user",
        record_kind="feature",
        record_id=feature,
        details={"feature": feature},
    )
    return {"status": "queued"}


def _document_dir(document: Document, files_root: Path) -> Path | None:
    """The document's on-disk folder, guarded against escaping the tenant files root."""
    if not document.storage_path:
        return None
    base = Path(document.storage_path).resolve()
    root = files_root.resolve()
    return base if (root == base or root in base.parents) else None


def _read_original(document: Document, files_root: Path) -> bytes:
    """Read a document's original bytes (raises 404 if its files are gone)."""
    base = _document_dir(document, files_root)
    if base is None:
        raise HTTPException(status_code=404, detail="document files not found")
    rel = document.metadata.get("original") or document.original_filename
    source = (base / str(rel)).resolve()
    if (source != base and base not in source.parents) or not source.is_file():
        raise HTTPException(status_code=404, detail="original file not found")
    return source.read_bytes()  # read before purging, so nothing is lost


def _purge_and_requeue(
    document: Document,
    data: bytes,
    files_root: Path,
    tenant_id: str,
    repo: DocumentRepository,
    jobs: IngestionJobRepository,
    ingest_subdir: str = "ingest",
) -> str:
    """Purge a document's rows + files, then drop ``data`` into an ingest folder to reprocess.

    ``ingest_subdir`` selects the intake folder - "ingest" (standard) or "ingest.enhanced" (the
    worker's Enhanced re-OCR services watch the latter)."""
    base = _document_dir(document, files_root)
    jobs.delete_for_document(tenant_id, document.id)
    repo.delete(tenant_id, document.id)  # FK-cascades chunks/entities/features/links/records
    if base is not None:
        shutil.rmtree(base, ignore_errors=True)
    ingest_dir = files_root / tenant_id / ingest_subdir
    ingest_dir.mkdir(parents=True, exist_ok=True)
    # Defense-in-depth: re-basename the stored filename so a crafted value can't escape ingest/.
    safe_name = Path(document.original_filename).name or "document"
    (ingest_dir / safe_name).write_bytes(data)
    return safe_name


@router.post("/{document_id}/reingest")
def reingest_document(
    document_id: str,
    request: Request,
    tenant: Tenant,
    repo: Repo,
    jobs: Jobs,
    audit: Audit,
    profile: Annotated[Literal["standard", "enhanced"], Query()] = "standard",
) -> dict[str, str]:
    """Re-ingest (re-OCR) a document: read its original, purge its rows/files, and drop it back in
    the ingest folder. ``profile=enhanced`` routes it to the slower, higher-quality OCR pass
    (heavier models + 300 DPI + orientation/unwarp), via the worker's ingest.enhanced/ folder."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    files_root = Path(request.app.state.settings.files_root)
    data = _read_original(document, files_root)
    subdir = "ingest.enhanced" if profile == "enhanced" else "ingest"
    # Record before the purge so the snapshot reads the still-present document row. The purge drops
    # the old document_id; the requeue creates a fresh one with its own trail under the same name.
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_REINGESTED,
        actor="user",
        actor_kind="user",
        document_id=document_id,
        description=f"Re-ingested by user ({profile} profile)",
        details={"profile": profile},
    )
    safe_name = _purge_and_requeue(document, data, files_root, tenant.tenant_id, repo, jobs, subdir)
    return {"status": "queued", "filename": safe_name, "profile": profile}


@router.post("/{document_id}/rotate")
def rotate_document(
    document_id: str,
    request: Request,
    tenant: Tenant,
    repo: Repo,
    jobs: Jobs,
    audit: Audit,
    degrees: Annotated[int, Query()] = 90,
) -> dict[str, str]:
    """Rotate the whole document clockwise (90/180/270) and re-ingest it upright. PDFs get a
    lossless /Rotate bump; images are re-encoded. The worker then re-OCRs the upright pages."""
    from doktok_modalities_files import rotate_source

    if degrees not in (90, 180, 270):
        raise HTTPException(status_code=422, detail="degrees must be 90, 180 or 270")
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    files_root = Path(request.app.state.settings.files_root)
    data = _read_original(document, files_root)
    try:
        rotated = rotate_source(data, document.detected_mime, degrees)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_ROTATED,
        actor="user",
        actor_kind="user",
        document_id=document_id,
        description=f"Rotated {degrees} clockwise and re-ingested",
        details={"degrees": degrees},
    )
    safe_name = _purge_and_requeue(document, rotated, files_root, tenant.tenant_id, repo, jobs)
    return {"status": "queued", "filename": safe_name, "degrees": str(degrees)}


@router.delete("/{document_id}")
def delete_document(
    document_id: str, request: Request, tenant: Tenant, repo: Repo, jobs: Jobs, audit: Audit
) -> dict[str, str]:
    """Delete a document, its files, and all its derived rows (chunks/entities/features/links/
    records via FK cascade)."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        # Idempotent: a retried DELETE of an already-removed document is a success, not a 404.
        return {"status": "deleted"}

    base = _document_dir(document, Path(request.app.state.settings.files_root))
    jobs.delete_for_document(tenant.tenant_id, document_id)
    repo.delete(tenant.tenant_id, document_id)  # FK-cascades derived rows
    # Record after the delete (so it only logs actual removals) with the identity passed explicitly:
    # the document row is gone, so the activity row carries the snapshot and survives on its own.
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_DELETED,
        actor="user",
        actor_kind="user",
        document_id=document_id,
        description="Deleted by user",
        doc_filename=document.original_filename,
        doc_title=document.title,
    )
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
