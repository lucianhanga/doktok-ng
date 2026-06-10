"""Activity/audit endpoints (M3.6). Tenant-scoped, read-only (ADR-0006/0007/0008)."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import AuditLogRepository
from doktok_contracts.schemas import AuditEvent
from fastapi import APIRouter, Depends, Query

from doktok_api.dependencies import Tenant, get_audit_repository

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

Repo = Annotated[AuditLogRepository, Depends(get_audit_repository)]


@router.get("", response_model=list[AuditEvent])
def list_activity(
    tenant: Tenant,
    repo: Repo,
    document_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEvent]:
    return repo.list_events(tenant.tenant_id, document_id=document_id, limit=limit, offset=offset)
