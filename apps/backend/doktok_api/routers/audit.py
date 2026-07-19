"""Activity/audit endpoints (M3.6). Tenant-scoped, read-only (ADR-0006/0007/0008)."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import AuditLogRepository
from doktok_contracts.schemas import AuditEvent
from doktok_core.security.roles import Role, role_at_least
from fastapi import APIRouter, Depends, Query, Request

from doktok_api.dependencies import Tenant, get_audit_repository, resolve_caller_role

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

Repo = Annotated[AuditLogRepository, Depends(get_audit_repository)]

# Non-admin callers see document/entity activity only (F-19, #633): auth/user/ops events carry
# login emails + client IPs and record administration, so the unfiltered feed is admin-only.
_VIEWER_EVENT_PREFIXES = ("document.", "feature.", "entity.")


@router.get("", response_model=list[AuditEvent])
def list_activity(
    request: Request,
    tenant: Tenant,
    repo: Repo,
    document_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEvent]:
    is_admin = role_at_least(resolve_caller_role(request, tenant), Role.ADMIN)
    return repo.list_events(
        tenant.tenant_id,
        document_id=document_id,
        limit=limit,
        offset=offset,
        event_type_prefixes=None if is_admin else _VIEWER_EVENT_PREFIXES,
    )
