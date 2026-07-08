"""Category vocabulary (M6.2). Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import CategoryRepository
from doktok_contracts.schemas import CategoryCoOccurrence, CategorySummary
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_category_repository

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])

Repo = Annotated[CategoryRepository, Depends(get_category_repository)]


@router.get("", response_model=list[CategorySummary])
def list_categories(tenant: Tenant, repo: Repo) -> list[CategorySummary]:
    """The tenant's active categories with how many documents carry each (vocabulary / filter)."""
    return repo.list_summary(tenant.tenant_id)


@router.get("/co-occurrence", response_model=list[CategoryCoOccurrence])
def list_co_occurrence(tenant: Tenant, repo: Repo) -> list[CategoryCoOccurrence]:
    """Unordered active-category pairs with the count of documents tagged with both."""
    return repo.category_co_occurrence(tenant.tenant_id)
