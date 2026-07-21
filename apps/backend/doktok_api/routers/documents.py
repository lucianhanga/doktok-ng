"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

import base64
import json
import shutil
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote

from doktok_contracts.ports import (
    AuditLogRepository,
    CategoryRepository,
    ChunkRepository,
    DocumentNoteRepository,
    DocumentRepository,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
    KnowledgeGraphRepository,
    RecordRepository,
    TagRepository,
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
    DocumentExtraction,
    DocumentFeature,
    DocumentIdSelection,
    DocumentLayout,
    DocumentListPage,
    DocumentListStats,
    DocumentNote,
    DocumentRecordPage,
    DocumentRelations,
    DocumentSort,
    DocumentStatus,
    EntityType,
    EntityTypeCount,
    LayoutLine,
    LayoutPage,
    ListAnchor,
    SimilarDocument,
    SortDir,
    Tag,
    TokenMatch,
)
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.documents.artifacts import NORMALIZED_PDF_REL, THUMBNAIL_REL
from doktok_core.features.catalog import FEATURE_CATALOG, FEATURE_GROUPS_BY_ID
from doktok_core.features.telemetry import build_processing_summary, build_processing_telemetry
from doktok_core.security.roles import Role
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from doktok_api.dependencies import (
    Tenant,
    get_audit_repository,
    get_category_repository,
    get_chunk_repository,
    get_document_note_repository,
    get_document_repository,
    get_entity_repository,
    get_feature_repository,
    get_job_repository,
    get_knowledge_graph_repository,
    get_record_repository,
    get_tag_repository,
    get_tenant_registry,
    resolve_caller_role,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

Repo = Annotated[DocumentRepository, Depends(get_document_repository)]
Notes = Annotated[DocumentNoteRepository, Depends(get_document_note_repository)]
TagRepo = Annotated[TagRepository, Depends(get_tag_repository)]
Entities = Annotated[EntityRepository, Depends(get_entity_repository)]
Chunks = Annotated[ChunkRepository, Depends(get_chunk_repository)]
Features = Annotated[FeatureRepository, Depends(get_feature_repository)]
Categories = Annotated[CategoryRepository, Depends(get_category_repository)]
Jobs = Annotated[IngestionJobRepository, Depends(get_job_repository)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]
Records = Annotated[RecordRepository, Depends(get_record_repository)]
Kg = Annotated[KnowledgeGraphRepository, Depends(get_knowledge_graph_repository)]

# Known reprocessable feature names (validated against incoming requests).
_CATALOG_FEATURE_NAMES: frozenset[str] = frozenset(spec.name for spec in FEATURE_CATALOG)

# Bytes of extracted text returned inline on the detail card; the full text is fetched lazily.
_EXCERPT_CHARS = 4000
# Highest-frequency entities surfaced on the card; the full list is fetched lazily.
_TOP_ENTITIES = 12
# Repeated opens of a document within this window collapse to a single document.viewed row.
_VIEW_DEDUP_SECONDS = 5


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
    elif isinstance(value, int):
        payload = "i:" + str(value)
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
        value: datetime | date | int | str | None
        if tag == "n":
            value = None
        elif tag == "t":
            value = datetime.fromisoformat(body)
        elif tag == "d":
            value = date.fromisoformat(body)
        elif tag == "i":
            value = int(body)
            # F-26 (#638): Python ints are unbounded but the value is cast ::bigint in Postgres;
            # an out-of-int64 cursor must hit the documented 400 contract, never a 500.
            if not -(2**63) <= value <= 2**63 - 1:
                raise ValueError("cursor value out of range")
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
    features: Features,
    entities: Entities,
    chunks: Chunks,
    categories: Categories,
    tag_repo: TagRepo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query(max_length=500)] = None,
    status: Annotated[DocumentStatus | None, Query()] = None,
    needs_attention: Annotated[bool, Query()] = False,
    unidentifiable: Annotated[bool | None, Query()] = None,
    sort: Annotated[DocumentSort, Query()] = DocumentSort.ACQUIRED,
    direction: Annotated[SortDir, Query(alias="dir")] = SortDir.DESC,
    title: Annotated[str | None, Query(max_length=500)] = None,
    token: Annotated[list[str] | None, Query()] = None,
    token_type: Annotated[EntityType | None, Query()] = None,
    token_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
    tag: Annotated[list[str] | None, Query()] = None,
    tag_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
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
        title=title,
        tokens=tokens,
        token_type=token_type,
        token_match=token_match,
        tag_ids=tuple(tag or ()),
        tag_match=tag_match,
    )
    doc_ids = [d.id for d in items]
    # Per-document processing summary for the list tooltip. The metadata-derived fields are free
    # from each row; the done/failed feature counts come from ONE batched GROUP BY over this page's
    # ids (no N+1), kept off the shared Document shape via the response envelope's sidecar map.
    feat_counts = features.feature_counts_for_documents(tenant.tenant_id, doc_ids)
    summaries = {
        d.id: build_processing_summary(
            d,
            features_done=feat_counts.get(d.id, (0, 0))[0],
            features_failed=feat_counts.get(d.id, (0, 0))[1],
        )
        for d in items
    }
    # Per-document entity/chunk counts + primary category for the Documents table columns.
    entity_counts = entities.entity_counts_for_documents(tenant.tenant_id, doc_ids)
    chunk_counts = chunks.chunk_counts_for_documents(tenant.tenant_id, doc_ids)
    primary_cats = categories.primary_categories(tenant.tenant_id, doc_ids)
    doc_stats = {
        d.id: DocumentListStats(
            entity_count=entity_counts.get(d.id, 0),
            chunk_count=chunk_counts.get(d.id, 0),
            category=primary_cats.get(d.id),
        )
        for d in items
    }
    return DocumentListPage(
        items=items,
        total=total,
        next_cursor=_encode_cursor(next_anchor),
        processing=summaries,
        stats=doc_stats,
        # Per-document tags for the list/cards chips (#549): one batched query over the page.
        tags=tag_repo.tags_for_documents(tenant.tenant_id, doc_ids),
    )


def _validated_tokens(token: list[str] | None) -> tuple[str, ...]:
    tokens = tuple(t for t in (token or []) if t.strip())
    if len(tokens) > _MAX_TOKENS:
        raise HTTPException(status_code=400, detail=f"at most {_MAX_TOKENS} tokens")
    return tokens


@router.get("/ids", response_model=DocumentIdSelection)
def list_document_ids(
    tenant: Tenant,
    repo: Repo,
    category: Annotated[str | None, Query(max_length=500)] = None,
    status: Annotated[DocumentStatus | None, Query()] = None,
    needs_attention: Annotated[bool, Query()] = False,
    unidentifiable: Annotated[bool | None, Query()] = None,
    title: Annotated[str | None, Query(max_length=500)] = None,
    token: Annotated[list[str] | None, Query()] = None,
    token_type: Annotated[EntityType | None, Query()] = None,
    token_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
    tag: Annotated[list[str] | None, Query()] = None,
    tag_match: Annotated[TokenMatch, Query()] = TokenMatch.ALL,
) -> DocumentIdSelection:
    """All document ids matching the filters (for 'select all matching' bulk actions)."""
    ids, total, truncated = repo.list_document_ids(
        tenant.tenant_id,
        status=status,
        category=category,
        needs_attention=needs_attention,
        unidentifiable=unidentifiable,
        title=title,
        tokens=_validated_tokens(token),
        token_type=token_type,
        token_match=token_match,
        tag_ids=tuple(tag or ()),
        tag_match=tag_match,
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
    records: Records,
    chunks: Chunks,
    tag_repo: TagRepo,
) -> DocumentDetail:
    """One aggregate for the detail card: document, processing, categories, an entity summary, a
    content excerpt, a structured-records rollup, and recent activity. The unbounded payloads - the
    full text, full entity list, and full records list - are fetched lazily via /content, /entities
    and /records when their tab is opened."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")

    # Log the view (every open of the detail card). A deterministic id bucketed to a short window
    # collapses duplicate opens - notably React StrictMode firing the detail GET twice on mount -
    # into a single row via the repository's insert-if-absent semantics.
    bucket = int(datetime.now(UTC).timestamp()) // _VIEW_DEDUP_SECONDS
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_VIEWED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        event_id=f"view-{tenant.tenant_id}-{document_id}-{bucket}",
    )

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

    feature_rows = features.list_for_document(tenant.tenant_id, document_id)
    # Index + extraction facts (#732): the chunk count from the chunk store, and the extraction
    # method/OCR confidence read from the document's content.json (defaults when missing).
    chunk_count = chunks.chunk_counts_for_documents(tenant.tenant_id, [document_id]).get(
        document_id, 0
    )
    extraction = DocumentExtraction()
    if document.storage_path:
        meta_path = Path(document.storage_path) / "content.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                extraction = DocumentExtraction(
                    method=str(meta.get("extraction_method") or ""),
                    ocr_confidence=meta.get("ocr_confidence"),
                )
            except (OSError, ValueError):
                pass  # a corrupt artifact never breaks the detail card
    return DocumentDetail(
        document=document,
        # Built from the document metadata + the already-loaded feature rows (no extra query).
        processing=build_processing_telemetry(document, feature_rows),
        features=feature_rows,
        categories=categories.list_for_document(tenant.tenant_id, document_id),
        entities=entity_summary,
        content=content,
        recent_activity=audit.list_events(tenant.tenant_id, document_id=document_id, limit=10),
        records=records.record_summary(tenant.tenant_id, document_id),
        chunk_count=chunk_count,
        extraction=extraction,
        tags=tag_repo.list_for_document(tenant.tenant_id, document_id),
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


@router.get("/{document_id}/relations", response_model=DocumentRelations)
def get_document_relations(
    document_id: str, tenant: Tenant, repo: Repo, kg: Kg
) -> DocumentRelations:
    """The document's knowledge-graph footprint (#731): its entity mentions resolved to their
    canonical nodes (label + type), and the relation edges touching at least one of those nodes."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return kg.relations_for_document(tenant.tenant_id, document_id)


@router.get("/{document_id}/similar", response_model=list[SimilarDocument])
def get_similar_documents(
    document_id: str,
    tenant: Tenant,
    repo: Repo,
    chunks: Chunks,
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
) -> list[SimilarDocument]:
    """A document's semantic neighbors (#730): other active documents ranked by chunk-embedding
    closeness (mean of per-chunk best cosine matches). Empty when there are no neighbors."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    similar = chunks.similar_documents(tenant.tenant_id, document_id, limit=limit)
    # The chunk store ranks by (document_id, score); identity fields are enriched from the
    # document repo (the InMemory store does not join documents).
    enriched: list[SimilarDocument] = []
    for s in similar:
        d = repo.get(tenant.tenant_id, s.document_id)
        enriched.append(
            SimilarDocument(
                document_id=s.document_id,
                title=d.title if d is not None else s.title,
                original_filename=d.original_filename if d is not None else s.original_filename,
                score=s.score,
            )
        )
    return enriched


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


@router.get("/{document_id}/records", response_model=DocumentRecordPage)
def get_document_records(
    document_id: str,
    tenant: Tenant,
    repo: Repo,
    records: Records,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentRecordPage:
    """Lazy, offset-paginated full list of a document's structured records (the Transactions tab).
    The eager rollup lives on /detail; this is the unbounded row list, fetched on demand. 404s when
    the document is missing or owned by another tenant (so 'no records' differs from 'no doc')."""
    if repo.get(tenant.tenant_id, document_id) is None:
        raise HTTPException(status_code=404, detail="document not found")
    items, total = records.list_for_document_page(
        tenant.tenant_id, document_id, limit=limit, offset=offset
    )
    served = offset + len(items)
    return DocumentRecordPage(
        items=items, total=total, next_offset=served if served < total else None
    )


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
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"{feature} re-queued by user",
        record_kind="feature",
        record_id=feature,
        details={"feature": feature},
    )
    return {"status": "queued"}


@router.post("/features/{feature}/reprocess-all")
def reprocess_all_documents_feature(
    feature: str, tenant: Tenant, repo: Repo, features: Features, audit: Audit
) -> dict[str, str | int]:
    """Re-queue one catalog feature for every document in the tenant's corpus.

    Validates that ``feature`` is a known reprocessable name, then calls
    ``features.reset`` for each document id in the tenant.  Only successful
    resets are counted.  A single audit row is written for the bulk action.
    """
    if feature not in _CATALOG_FEATURE_NAMES:
        raise HTTPException(status_code=404, detail="unknown feature")
    ids, _total, _truncated = repo.list_document_ids(tenant.tenant_id)
    count = sum(1 for doc_id in ids if features.reset(tenant.tenant_id, doc_id, feature))
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.FEATURE_RETRIED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=f"{feature} re-queued for {count} documents",
        record_kind="feature",
        record_id=feature,
        details={"feature": feature, "count": count},
    )
    return {"status": "queued", "count": count}


class GroupReprocessResponse(BaseModel):
    """Response for a successful group reprocess request."""

    status: str
    count: int
    features: list[str]


@router.post(
    "/features/group/{group}/reprocess-all",
    response_model=GroupReprocessResponse,
)
def reprocess_all_documents_group(
    group: str, tenant: Tenant, repo: Repo, features: Features, audit: Audit
) -> GroupReprocessResponse:
    """Re-queue all features in a named group for every document in the tenant's corpus.

    ``group`` must be a known group id (else 404).  Every feature in the group's reprocess_set is
    reset for each tenant document; the reconciler then runs them in dependency order automatically.
    A single audit row is written for the bulk action.  Returns ``{status, count, features}`` where
    ``count`` is the number of documents for which at least one feature reset succeeded and
    ``features`` is the full reprocess_set that was targeted.
    """
    feature_group = FEATURE_GROUPS_BY_ID.get(group)
    if feature_group is None:
        raise HTTPException(status_code=404, detail="unknown feature group")
    ids, _total, _truncated = repo.list_document_ids(tenant.tenant_id)
    count = 0
    for doc_id in ids:
        # Reset ALL features in the set for this document (no short-circuit) so the reconciler
        # sees every feature as pending.  Count the document if any reset succeeded.
        results = [
            features.reset(tenant.tenant_id, doc_id, feat) for feat in feature_group.reprocess_set
        ]
        if any(results):
            count += 1
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.FEATURE_RETRIED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=f"reprocess group {feature_group.label} ({count} docs)",
        record_kind="feature",
        record_id=group,
        details={"group": group, "features": list(feature_group.reprocess_set), "count": count},
    )
    return GroupReprocessResponse(
        status="queued",
        count=count,
        features=list(feature_group.reprocess_set),
    )


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
        actor=actor_identity(tenant),
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
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Rotated {degrees} clockwise and re-ingested",
        details={"degrees": degrees},
    )
    safe_name = _purge_and_requeue(document, rotated, files_root, tenant.tenant_id, repo, jobs)
    return {"status": "queued", "filename": safe_name, "degrees": str(degrees)}


class TitleUpdate(BaseModel):
    """Rename request (#537): a title, trimmed, bounded."""

    title: str = Field(min_length=1, max_length=200)


@router.patch("/{document_id}/title", response_model=Document)
def rename_document(
    document_id: str, update: TitleUpdate, tenant: Tenant, repo: Repo, audit: Audit
) -> Document:
    """Rename a document (#537): the title is marked ``title_source='manual'`` so the
    doc_metadata feature never overwrites it on reprocessing. Audited (old/new title)."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    title = update.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty or whitespace")
    repo.set_title(tenant.tenant_id, document_id, title)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_RENAMED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Renamed to '{title}'",
        details={"old_title": document.title or document.original_filename, "new_title": title},
    )
    updated = repo.get(tenant.tenant_id, document_id)
    assert updated is not None  # the row existed a statement ago, same transaction scope
    return updated


@router.delete("/{document_id}/title", response_model=Document)
def reset_document_title(document_id: str, tenant: Tenant, repo: Repo, audit: Audit) -> Document:
    """Hand a renamed title back to the auto path (#537): ``title_source='auto'``; the current
    title stays until the next doc_metadata run re-derives it."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    repo.clear_manual_title(tenant.tenant_id, document_id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_RENAMED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description="Title reset to automatic",
        details={"old_title": document.title or document.original_filename, "new_title": None},
    )
    updated = repo.get(tenant.tenant_id, document_id)
    assert updated is not None
    return updated


# --- Document notes (#736): immutable, timestamped entries; deletions are audit-logged. ---


class NoteCreate(BaseModel):
    """Add-note request (#736): free text, trimmed, bounded."""

    body: str = Field(min_length=1, max_length=2000)


@router.get("/{document_id}/notes", response_model=list[DocumentNote])
def list_document_notes(
    document_id: str, tenant: Tenant, repo: Repo, notes: Notes
) -> list[DocumentNote]:
    """The document's notes, newest first (#736). Any authenticated reader of the tenant."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return notes.list_for_document(tenant.tenant_id, document_id)


@router.post("/{document_id}/notes", response_model=DocumentNote, status_code=201)
def add_document_note(
    document_id: str,
    payload: NoteCreate,
    request: Request,
    tenant: Tenant,
    repo: Repo,
    notes: Notes,
    audit: Audit,
) -> DocumentNote:
    """Add a note (#736, editor role via the router write guard). The author is the session user
    (the static host token records the console identity); the addition is audited."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="note body must not be empty or whitespace")
    registry = get_tenant_registry(request)
    user = registry.get_user(tenant.tenant_id, tenant.user_id) if tenant.user_id else None
    note = DocumentNote(
        id=uuid.uuid4().hex,
        tenant_id=tenant.tenant_id,
        document_id=document_id,
        author_id=tenant.user_id or tenant.tenant_id,
        author_email=user.email if user else actor_identity(tenant),
        body=body,
        created_at=datetime.now(UTC),
    )
    notes.add_note(note)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_NOTE_ADDED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Note added by {note.author_email}",
        details={"note_id": note.id},
    )
    return note


@router.delete("/{document_id}/notes/{note_id}", status_code=204)
def delete_document_note(
    document_id: str,
    note_id: str,
    request: Request,
    tenant: Tenant,
    notes: Notes,
    audit: Audit,
) -> Response:
    """Delete a note (#736): only its author or an admin. The deletion is audited WITH a body
    snapshot, so the trail outlives the note."""
    note = notes.get_note(tenant.tenant_id, note_id)
    if note is None or note.document_id != document_id:
        raise HTTPException(status_code=404, detail="note not found")
    role = resolve_caller_role(request, tenant)
    if note.author_id != tenant.user_id and role is not Role.ADMIN:
        raise HTTPException(status_code=403, detail="only the author or an admin can delete a note")
    notes.delete_note(tenant.tenant_id, note_id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_NOTE_DELETED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Note by {note.author_email} deleted by {actor_identity(tenant)}",
        details={"note_id": note_id, "note_author": note.author_email, "body": note.body},
    )
    return Response(status_code=204)


# --- Document tag assignment (#546): single idempotent assign/unassign + bounded bulk. ---


@router.get("/{document_id}/tags", response_model=list[Tag])
def list_document_tags(
    document_id: str, tenant: Tenant, repo: Repo, tag_repo: TagRepo
) -> list[Tag]:
    """The document's tags (#546). Any authenticated reader of the tenant."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return tag_repo.list_for_document(tenant.tenant_id, document_id)


@router.put("/{document_id}/tags/{tag_id}", status_code=204)
def assign_document_tag(
    document_id: str, tag_id: str, tenant: Tenant, repo: Repo, tag_repo: TagRepo, audit: Audit
) -> Response:
    """Assign a tag to a document (#546, editor via the router write guard). Idempotent; audited
    per document."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    tag = tag_repo.get_tag(tenant.tenant_id, tag_id)
    if tag is None or tag.status != "active":
        raise HTTPException(status_code=404, detail="tag not found")
    already = any(t.id == tag_id for t in tag_repo.list_for_document(tenant.tenant_id, document_id))
    if already:
        return Response(status_code=204)  # idempotent no-op: no audit row for a non-change
    tag_repo.link(tenant.tenant_id, document_id, tag_id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_TAGGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Tagged with '{tag.name}'",
        details={"tag_id": tag_id, "tag_name": tag.name},
    )
    return Response(status_code=204)


@router.delete("/{document_id}/tags/{tag_id}", status_code=204)
def unassign_document_tag(
    document_id: str, tag_id: str, tenant: Tenant, repo: Repo, tag_repo: TagRepo, audit: Audit
) -> Response:
    """Remove a tag from a document (#546). Idempotent; audited per document."""
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    tag = tag_repo.get_tag(tenant.tenant_id, tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="tag not found")
    already = any(t.id == tag_id for t in tag_repo.list_for_document(tenant.tenant_id, document_id))
    if not already:
        return Response(status_code=204)  # idempotent no-op: nothing to remove
    tag_repo.unlink(tenant.tenant_id, document_id, tag_id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_UNTAGGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        document_id=document_id,
        description=f"Tag '{tag.name}' removed",
        details={"tag_id": tag_id, "tag_name": tag.name},
    )
    return Response(status_code=204)


class BulkTagUpdate(BaseModel):
    """Bulk assign/unassign (#546): add and remove tag sets over a bounded document-id set."""

    document_ids: list[str] = Field(min_length=1, max_length=500)
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@router.post("/tags:bulk")
def bulk_update_document_tags(
    payload: BulkTagUpdate, tenant: Tenant, repo: Repo, tag_repo: TagRepo, audit: Audit
) -> dict[str, int]:
    """Assign/unassign tags across up to 500 documents (#546): set semantics (add is idempotent,
    remove of an unlinked tag is a no-op), ONE summary audit row per direction."""
    for tag_id in {*payload.add, *payload.remove}:
        if tag_repo.get_tag(tenant.tenant_id, tag_id) is None:
            raise HTTPException(status_code=404, detail=f"tag not found: {tag_id}")
    docs = repo.get_many(tenant.tenant_id, payload.document_ids)
    by_id = {d.id for d in docs}
    # Unknown ids are skipped silently (the set may be a stale UI snapshot); the rest is updated.
    for document_id in payload.document_ids:
        if document_id not in by_id:
            continue
        for tag_id in payload.add:
            tag_repo.link(tenant.tenant_id, document_id, tag_id)
        for tag_id in payload.remove:
            tag_repo.unlink(tenant.tenant_id, document_id, tag_id)
    if payload.add:
        record_activity(
            audit,
            tenant.tenant_id,
            AuditEventType.DOCUMENT_TAGGED,
            actor=actor_identity(tenant),
            actor_kind="user",
            description=f"Tagged {len(by_id)} document(s) with {len(payload.add)} tag(s)",
            details={
                "document_ids": sorted(by_id),
                "tag_ids": payload.add,
                "document_count": len(by_id),
            },
        )
    if payload.remove:
        record_activity(
            audit,
            tenant.tenant_id,
            AuditEventType.DOCUMENT_UNTAGGED,
            actor=actor_identity(tenant),
            actor_kind="user",
            description=f"Removed {len(payload.remove)} tag(s) from {len(by_id)} document(s)",
            details={
                "document_ids": sorted(by_id),
                "tag_ids": payload.remove,
                "document_count": len(by_id),
            },
        )
    return {"updated": len(by_id)}


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    tenant: Tenant,
    repo: Repo,
    jobs: Jobs,
    audit: Audit,
    kg: Kg,
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
    # The cascade removes the document's kg_entity_mentions, but a canonical KG node is the FK
    # parent of that link, so a node whose mentions all came from this document is left orphaned.
    # Purge the KG footprint: prune orphaned nodes (edges cascade) + clear its edge provenance.
    kg.purge_document(tenant.tenant_id, document_id)
    # Record after the delete (so it only logs actual removals) with the identity passed explicitly:
    # the document row is gone, so the activity row carries the snapshot and survives on its own.
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.DOCUMENT_DELETED,
        actor=actor_identity(tenant),
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
