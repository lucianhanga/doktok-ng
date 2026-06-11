"""Faceted token search endpoints (autocomplete + AND search). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import EntityRepository
from doktok_contracts.schemas import Document, TokenSuggestion
from fastapi import APIRouter, Depends, Query

from doktok_api.dependencies import Tenant, get_entity_repository

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])

Repo = Annotated[EntityRepository, Depends(get_entity_repository)]


@router.get("/suggest", response_model=list[TokenSuggestion])
def suggest(
    tenant: Tenant,
    repo: Repo,
    prefix: Annotated[str, Query()] = "",
    token: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[TokenSuggestion]:
    """Tokens starting with ``prefix`` (case-insensitive), narrowed to docs with all ``token``."""
    return repo.suggest_tokens(tenant.tenant_id, prefix, selected=token or [], limit=limit)


@router.get("/search", response_model=list[Document])
def search(
    tenant: Tenant,
    repo: Repo,
    token: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    """Documents containing ALL ``token`` values (AND, case-insensitive)."""
    tokens = token or []
    if not tokens:
        return []
    return repo.documents_for_tokens(tenant.tenant_id, tokens, limit=limit, offset=offset)
