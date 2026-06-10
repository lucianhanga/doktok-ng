"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import DocumentRepository
from doktok_contracts.schemas import Document
from fastapi import APIRouter, Depends, HTTPException, Query

from doktok_api.dependencies import Tenant, get_document_repository

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

Repo = Annotated[DocumentRepository, Depends(get_document_repository)]


@router.get("", response_model=list[Document])
def list_documents(
    tenant: Tenant,
    repo: Repo,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Document]:
    return repo.list_documents(tenant.tenant_id, limit=limit, offset=offset)


@router.get("/{document_id}", response_model=Document)
def get_document(document_id: str, tenant: Tenant, repo: Repo) -> Document:
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document
