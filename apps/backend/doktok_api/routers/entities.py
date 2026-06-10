"""Entity endpoints (brief section 22). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import EntityRepository
from doktok_contracts.schemas import Document, EntitySummary, EntityType
from fastapi import APIRouter, Depends, Query

from doktok_api.dependencies import Tenant, get_entity_repository

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])

Repo = Annotated[EntityRepository, Depends(get_entity_repository)]


@router.get("", response_model=list[EntitySummary])
def list_entities(
    tenant: Tenant,
    repo: Repo,
    entity_type: Annotated[EntityType | None, Query(alias="type")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EntitySummary]:
    return repo.list_distinct(tenant.tenant_id, entity_type=entity_type, limit=limit, offset=offset)


@router.get("/documents", response_model=list[Document])
def documents_for_entity(
    tenant: Tenant,
    repo: Repo,
    entity_type: Annotated[EntityType, Query(alias="type")],
    value: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    return repo.documents_for_entity(
        tenant.tenant_id, entity_type, value, limit=limit, offset=offset
    )
