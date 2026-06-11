"""Tenant-wide feature ledger (for document-list badges). Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import FeatureRepository
from doktok_contracts.schemas import DocumentFeature
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_feature_repository

router = APIRouter(prefix="/api/v1/features", tags=["features"])

Repo = Annotated[FeatureRepository, Depends(get_feature_repository)]


@router.get("", response_model=list[DocumentFeature])
def list_features(tenant: Tenant, repo: Repo) -> list[DocumentFeature]:
    """All feature-ledger rows for the tenant (the UI groups them by document for badges)."""
    return repo.list_for_tenant(tenant.tenant_id)
