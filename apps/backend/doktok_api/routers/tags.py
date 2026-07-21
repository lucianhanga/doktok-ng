"""Tag CRUD endpoints (epic #543, #545). Tenant-scoped; mutations need the editor role via the
router-level write guard and pass the TagManager seam (a future tags:manage role check swaps in
here, per the epic's architecture note)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from doktok_contracts.ports import AuditLogRepository, TagRepository
from doktok_contracts.schemas import AuditEventType, Tag, TagCreate, TagOut, TagUpdate
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.tags import TAG_PALETTE, normalize_tag_name
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.errors import IntegrityError
from pydantic import BaseModel, Field

from doktok_api.dependencies import Tenant, get_audit_repository, get_tag_repository

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])

Repo = Annotated[TagRepository, Depends(get_tag_repository)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]

# The TagManager seam (#545): mutations resolve through this dependency so a future role check
# (tags:manage, Tenant & User Management epic #523) is a one-line swap, not a refactor.
TagManager = Tenant


def _out(tag: Tag, count: int) -> TagOut:
    return TagOut(**tag.model_dump(), document_count=count)


@router.get("", response_model=list[TagOut])
def list_tags(tenant: TagManager, repo: Repo, q: str = "") -> list[TagOut]:
    """Active tags with their document counts (#545); ``q`` filters by name (case-insensitive)."""
    counts = repo.tag_counts(tenant.tenant_id)
    tags = repo.list_tags(tenant.tenant_id)
    needle = q.strip().casefold()
    if needle:
        tags = [t for t in tags if needle in t.name.casefold() or needle in t.normalized]
    return [_out(t, counts.get(t.id, 0)) for t in tags]


@router.post("", response_model=TagOut, status_code=201)
def create_tag(payload: TagCreate, tenant: TagManager, repo: Repo, audit: Audit) -> TagOut:
    """Create a tag (#545): normalized dedup (exact = 409 duplicate; token-set near-miss = 409
    'did you mean' unless ``allow_similar``), palette-token validation, tenant cap (DB trigger)."""
    normalized = normalize_tag_name(payload.name)
    if not normalized:
        raise HTTPException(status_code=422, detail="tag name must not be empty")
    if payload.color not in TAG_PALETTE:
        raise HTTPException(
            status_code=422,
            detail=f"color must be one of the palette tokens: {', '.join(TAG_PALETTE)}",
        )
    exact = repo.find_by_normalized(tenant.tenant_id, normalized)
    if exact is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate", "existing": {"id": exact.id, "name": exact.name}},
        )
    if not payload.allow_similar:
        similar = repo.find_similar(tenant.tenant_id, normalized)
        if similar:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "similar",
                    "message": (
                        "a similar tag exists - did you mean one of these? "
                        "(resend with allow_similar=true to create anyway)"
                    ),
                    "similar": [{"id": t.id, "name": t.name} for t in similar],
                },
            )
    tag = Tag(
        id=uuid.uuid4().hex,
        tenant_id=tenant.tenant_id,
        name=payload.name.strip(),
        normalized=normalized,
        description=payload.description.strip(),
        color=payload.color,
        created_at=datetime.now(UTC),
    )
    try:
        repo.create_tag(tag)
    except IntegrityError as exc:
        # The tenant cap trigger (100 active tags) reports as a check violation.
        if "100 active tags" in str(exc):
            raise HTTPException(
                status_code=409, detail="tenant already has 100 active tags"
            ) from exc
        raise
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.TAG_CREATED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=f"Tag '{tag.name}' created",
        details={"tag_id": tag.id},
    )
    return _out(tag, 0)


@router.patch("/{tag_id}", response_model=TagOut)
def update_tag(
    tag_id: str, payload: TagUpdate, tenant: TagManager, repo: Repo, audit: Audit
) -> TagOut:
    """Rename / re-describe / recolor a tag (#545); a rename re-normalizes and re-checks
    uniqueness (excluding the tag itself)."""
    tag = repo.get_tag(tenant.tenant_id, tag_id)
    if tag is None or tag.status != "active":
        raise HTTPException(status_code=404, detail="tag not found")
    if payload.color is not None and payload.color not in TAG_PALETTE:
        raise HTTPException(
            status_code=422,
            detail=f"color must be one of the palette tokens: {', '.join(TAG_PALETTE)}",
        )
    new_name: str | None = None
    new_normalized: str | None = None
    if payload.name is not None:
        new_name = payload.name.strip()
        new_normalized = normalize_tag_name(payload.name)
        if not new_normalized:
            raise HTTPException(status_code=422, detail="tag name must not be empty")
        existing = repo.find_by_normalized(tenant.tenant_id, new_normalized)
        if existing is not None and existing.id != tag_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate",
                    "existing": {"id": existing.id, "name": existing.name},
                },
            )
    updated = repo.update_tag(
        tenant.tenant_id,
        tag_id,
        name=new_name,
        normalized=new_normalized,
        description=payload.description.strip() if payload.description is not None else None,
        color=payload.color,
    )
    assert updated is not None  # the row existed a statement ago
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.TAG_UPDATED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=f"Tag '{updated.name}' updated",
        details={"tag_id": tag_id},
    )
    return _out(updated, repo.document_count(tenant.tenant_id, tag_id))


@router.delete("/{tag_id}", status_code=204)
def delete_tag(
    tag_id: str,
    tenant: TagManager,
    repo: Repo,
    audit: Audit,
    force: Annotated[bool, Query()] = False,
) -> Response:
    """Delete a tag (#545): in use → 409 with the document count, ``force=true`` unlinks +
    deletes (audited as a warning - it changes every user's view). Idempotent."""
    tag = repo.get_tag(tenant.tenant_id, tag_id)
    if tag is None:
        return Response(status_code=204)
    count = repo.document_count(tenant.tenant_id, tag_id)
    if count > 0 and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "in_use",
                "document_count": count,
                "message": (
                    f"tag is used on {count} document(s); resend with force=true to delete anyway"
                ),
            },
        )
    repo.delete_tag(tenant.tenant_id, tag_id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.TAG_DELETED,
        actor=actor_identity(tenant),
        actor_kind="user",
        severity="warning" if count > 0 else "info",
        description=(
            f"Tag '{tag.name}' deleted" + (f" (in use on {count} document(s))" if count else "")
        ),
        details={"tag_id": tag_id, "document_count": count},
    )
    return Response(status_code=204)


class TagMergeRequest(BaseModel):
    """Merge request (#550): the surviving tag the loser folds into."""

    survivor_id: str = Field(min_length=1)


@router.post("/{tag_id}/merge", status_code=204)
def merge_tag(
    tag_id: str, payload: TagMergeRequest, tenant: TagManager, repo: Repo, audit: Audit
) -> Response:
    """Merge one tag into a survivor (#550): repoint the loser's document links (de-duped on the
    PK), mark the loser merged (hidden from active lists), and write the merge log row
    (method='manual')."""
    loser = repo.get_tag(tenant.tenant_id, tag_id)
    if loser is None or loser.status != "active":
        raise HTTPException(status_code=404, detail="tag not found")
    survivor = repo.get_tag(tenant.tenant_id, payload.survivor_id)
    if survivor is None or survivor.status != "active":
        raise HTTPException(status_code=404, detail="survivor tag not found")
    if loser.id == survivor.id:
        raise HTTPException(status_code=422, detail="a tag cannot be merged into itself")
    moved = repo.merge_into(tenant.tenant_id, loser.id, survivor.id)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.TAG_MERGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=f"Tag '{loser.name}' merged into '{survivor.name}'",
        details={
            "loser_id": loser.id,
            "loser_name": loser.name,
            "survivor_id": survivor.id,
            "survivor_name": survivor.name,
            "method": "manual",
            "documents_moved": moved,
        },
    )
    return Response(status_code=204)
