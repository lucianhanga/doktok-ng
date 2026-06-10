"""Ingestion job endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import IngestionJobRepository
from doktok_contracts.schemas import IngestionJob
from fastapi import APIRouter, Depends, HTTPException, Query

from doktok_api.dependencies import Tenant, get_job_repository

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])

Repo = Annotated[IngestionJobRepository, Depends(get_job_repository)]


@router.get("/jobs", response_model=list[IngestionJob])
def list_jobs(
    tenant: Tenant,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IngestionJob]:
    return repo.list_jobs(tenant.tenant_id, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=IngestionJob)
def get_job(job_id: str, tenant: Tenant, repo: Repo) -> IngestionJob:
    job = repo.get(tenant.tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
