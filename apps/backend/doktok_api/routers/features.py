"""Tenant-wide feature ledger (for document-list badges). Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import FeatureRepository
from doktok_contracts.schemas import DocumentFeature
from doktok_core.features.catalog import FEATURE_CATALOG, FEATURE_GROUPS
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from doktok_api.dependencies import Tenant, get_feature_repository

router = APIRouter(prefix="/api/v1/features", tags=["features"])

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
        FeatureGroupEntry(id=g.id, label=g.label, badge_members=list(g.badge_members))
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
