"""Tenant-wide feature ledger (for document-list badges). Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import FeatureRepository
from doktok_contracts.schemas import DocumentFeature
from doktok_core.features.catalog import FEATURE_CATALOG
from fastapi import APIRouter, Depends
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
def list_features(tenant: Tenant, repo: Repo) -> list[DocumentFeature]:
    """All feature-ledger rows for the tenant (the UI groups them by document for badges)."""
    return repo.list_for_tenant(tenant.tenant_id)
