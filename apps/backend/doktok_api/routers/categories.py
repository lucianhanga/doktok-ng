"""Category vocabulary (M6.2). Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import CategoryRepository
from doktok_contracts.schemas import CategorySummary
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_category_repository

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])

Repo = Annotated[CategoryRepository, Depends(get_category_repository)]


@router.get("", response_model=list[CategorySummary])
def list_categories(tenant: Tenant, repo: Repo) -> list[CategorySummary]:
    """The tenant's active categories with how many documents carry each (vocabulary / filter)."""
    return repo.list_summary(tenant.tenant_id)
