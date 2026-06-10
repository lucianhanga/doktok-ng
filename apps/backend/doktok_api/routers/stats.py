"""Overview stats endpoint. Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import StatsRepository
from doktok_contracts.schemas import StatsSummary
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_stats_repository

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

Repo = Annotated[StatsRepository, Depends(get_stats_repository)]


@router.get("", response_model=StatsSummary)
def stats(tenant: Tenant, repo: Repo) -> StatsSummary:
    return repo.summary(tenant.tenant_id)
