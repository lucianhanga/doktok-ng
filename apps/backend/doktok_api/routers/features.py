"""Tenant-wide feature ledger (for document-list badges). Tenant-scoped, read-only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from doktok_contracts.ports import DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentFeature, DocumentStatus, FeatureStatus
from doktok_core.features.catalog import FEATURE_CATALOG, FEATURE_DEPENDENCIES, FEATURE_GROUPS
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from doktok_api.dependencies import Tenant, get_document_repository, get_feature_repository

router = APIRouter(prefix="/api/v1/features", tags=["features"])


_PREREQUISITES: dict[str, list[str]] = {}
for _feature, _prerequisite in FEATURE_DEPENDENCIES:
    _PREREQUISITES.setdefault(_feature, []).append(_prerequisite)


def _has_actionable_features(rows: list[DocumentFeature]) -> bool:
    by_name = {row.feature: row for row in rows}

    def actionable(name: str, seen: frozenset[str]) -> bool:
        row = by_name.get(name)
        if row is None:
            return True  # legacy/partial ledgers have no failure evidence for this prerequisite
        if row.status is FeatureStatus.DONE:
            return True
        if row.status is FeatureStatus.RUNNING:
            return True
        if row.status is FeatureStatus.FAILED:
            return row.attempts < row.max_attempts
        if row.status is not FeatureStatus.PENDING or name in seen:
            return False
        return all(
            actionable(prerequisite, seen | {name}) for prerequisite in _PREREQUISITES.get(name, [])
        )

    return any(
        row.status is not FeatureStatus.DONE and actionable(row.feature, frozenset())
        for row in rows
    )


Repo = Annotated[FeatureRepository, Depends(get_feature_repository)]


class FeatureCatalogEntry(BaseModel):
    """A reprocessable feature, for the UI's reprocess dropdown."""

    name: str
    version: int
    label: str
    description: str


class FeatureGroupEntry(BaseModel):
    """A logical feature group for badge aggregation and group reprocess actions."""

    id: str
    label: str
    badge_members: list[str]
    # The full set reset on a group reprocess - wider than badge_members when re-extraction
    # auto-invalidates downstream features (Entities also rebuilds entity_graph + relations). The UI
    # uses this so a per-document group reprocess chains the same features as the all-docs action.
    reprocess_set: list[str]


@router.get("/groups", response_model=list[FeatureGroupEntry])
def feature_groups(tenant: Tenant) -> list[FeatureGroupEntry]:
    """The KG-related feature groups for badge aggregation and group reprocess actions.

    The frontend derives two things from this response:
    - which feature names to collapse into each group badge
    - which group ids to pass to POST /api/v1/documents/features/group/{group}/reprocess-all
    No hardcoding of group structure in the UI.
    """
    _ = tenant  # auth-gated; the group definitions are the same for every tenant
    return [
        FeatureGroupEntry(
            id=g.id,
            label=g.label,
            badge_members=list(g.badge_members),
            reprocess_set=list(g.reprocess_set),
        )
        for g in FEATURE_GROUPS
    ]


@router.get("/catalog", response_model=list[FeatureCatalogEntry])
def feature_catalog(tenant: Tenant) -> list[FeatureCatalogEntry]:
    """The features that can be reprocessed on demand (have a reconciler processor)."""
    _ = tenant  # auth-gated; the catalog itself is the same for every tenant
    return [
        FeatureCatalogEntry(
            name=spec.name, version=spec.version, label=spec.label, description=spec.description
        )
        for spec in FEATURE_CATALOG
    ]


@router.get("", response_model=list[DocumentFeature])
def list_features(
    tenant: Tenant,
    repo: Repo,
    document_ids: Annotated[str | None, Query()] = None,
) -> list[DocumentFeature]:
    """Feature-ledger rows the UI groups by document for badges.

    Pass ``document_ids`` (comma-separated) to scope the result to the documents currently on screen
    - the badge view must cover exactly those, and an unscoped tenant query is row-capped, which can
    drop the newest documents' badges once a tenant has many documents. Without it, returns the
    (capped) tenant-wide ledger for backward compatibility.
    """
    if document_ids is not None:
        ids = [d for d in (s.strip() for s in document_ids.split(",")) if d]
        return repo.list_for_documents(tenant.tenant_id, ids)
    return repo.list_for_tenant(tenant.tenant_id)


class FeatureStageState(BaseModel):
    """One stage's ledger state within an in-flight document."""

    feature: str
    status: str


class InflightDocument(BaseModel):
    """A document still moving through the stage ledger (status ``processing``), with the
    per-stage states that drive the overview's granular progress bar (#848)."""

    document_id: str
    filename: str
    title: str | None
    created_at: datetime
    features: list[FeatureStageState]


DocRepo = Annotated[DocumentRepository, Depends(get_document_repository)]


@router.get("/inflight", response_model=list[InflightDocument])
def inflight_documents(tenant: Tenant, docs: DocRepo, repo: Repo) -> list[InflightDocument]:
    """Documents still being processed, plus each stage's ledger state.

    "Still being processed" = status ``processing`` OR any stage row with actionable work:
    extract activates the document long before enrichment finishes, and the row must stay in the
    overview until the whole ledger is done (#848, user report 2026-10-25). Terminal-only and
    permanently blocked documents surface under Needs attention instead.
    """
    now = datetime.now(UTC)
    by_id: dict[str, InflightDocument] = {}

    def emit(doc: Document, rows: list[DocumentFeature]) -> None:
        by_id[doc.id] = InflightDocument(
            document_id=doc.id,
            filename=doc.original_filename,
            title=doc.title,
            created_at=doc.created_at,
            features=[
                FeatureStageState(feature=row.feature, status=row.status.value) for row in rows
            ],
        )

    for doc in docs.list_processing(tenant.tenant_id, older_than=now):
        rows = repo.list_for_document(tenant.tenant_id, doc.id)
        # A processing document with no ledger rows is still in pre-intake work.
        if rows and not _has_actionable_features(rows):
            continue
        emit(doc, rows)
    for doc_id in repo.documents_with_open_features(tenant.tenant_id):
        if doc_id in by_id:
            continue
        candidate = docs.get(tenant.tenant_id, doc_id)
        # Only genuinely in-flight states: a failed/quarantined document with leftover open rows
        # is shown under Needs attention, not here.
        if candidate is not None and candidate.status in (
            DocumentStatus.PROCESSING,
            DocumentStatus.ACTIVE,
        ):
            rows = repo.list_for_document(tenant.tenant_id, candidate.id)
            if _has_actionable_features(rows):
                emit(candidate, rows)
    return sorted(by_id.values(), key=lambda d: d.created_at)
