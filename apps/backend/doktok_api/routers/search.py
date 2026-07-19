"""Hybrid search endpoint (brief section 17). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import Retriever
from doktok_contracts.schemas import SearchHit
from fastapi import APIRouter, Depends, Query

from doktok_api.dependencies import Tenant, get_retriever

router = APIRouter(prefix="/api/v1/search", tags=["search"])

Ret = Annotated[Retriever, Depends(get_retriever)]


@router.get("", response_model=list[SearchHit])
def search(
    tenant: Tenant,
    retriever: Ret,
    q: Annotated[str, Query(min_length=1, max_length=500, description="search query")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SearchHit]:
    return retriever.search(tenant.tenant_id, q, limit)
