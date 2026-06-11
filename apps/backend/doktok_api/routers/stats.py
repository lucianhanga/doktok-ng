"""Overview stats endpoint. Tenant-scoped, read-only."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import StatsRepository
from doktok_contracts.schemas import StatsSummary
from doktok_core.ingestion.layout import FilesystemLayout
from fastapi import APIRouter, Depends, Request

from doktok_api.dependencies import Tenant, get_stats_repository

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

Repo = Annotated[StatsRepository, Depends(get_stats_repository)]


@router.get("", response_model=StatsSummary)
def stats(request: Request, tenant: Tenant, repo: Repo) -> StatsSummary:
    summary = repo.summary(tenant.tenant_id)
    files_root = request.app.state.settings.files_root
    summary.pending_ingest = FilesystemLayout(files_root, tenant.tenant_id).pending_ingest_count()
    return summary
