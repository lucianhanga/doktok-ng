"""Document endpoints (brief section 22). Tenant-scoped (ADR-0007/0008)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from doktok_contracts.ports import DocumentRepository, EntityRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentContent, DocumentEntity, DocumentFeature
from fastapi import APIRouter, Depends, HTTPException, Query

from doktok_api.dependencies import (
    Tenant,
    get_document_repository,
    get_entity_repository,
    get_feature_repository,
)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

Repo = Annotated[DocumentRepository, Depends(get_document_repository)]
Entities = Annotated[EntityRepository, Depends(get_entity_repository)]
Features = Annotated[FeatureRepository, Depends(get_feature_repository)]


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


@router.get("/{document_id}/content", response_model=DocumentContent)
def get_document_content(document_id: str, tenant: Tenant, repo: Repo) -> DocumentContent:
    document = repo.get(tenant.tenant_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    content = ""
    if document.storage_path:
        path = Path(document.storage_path) / "content.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
    return DocumentContent(document_id=document_id, content=content)


@router.get("/{document_id}/entities", response_model=list[DocumentEntity])
def get_document_entities(
    document_id: str, tenant: Tenant, entities: Entities
) -> list[DocumentEntity]:
    return entities.list_for_document(tenant.tenant_id, document_id)


@router.get("/{document_id}/features", response_model=list[DocumentFeature])
def get_document_features(
    document_id: str, tenant: Tenant, features: Features
) -> list[DocumentFeature]:
    return features.list_for_document(tenant.tenant_id, document_id)


@router.post("/{document_id}/features/{feature}/retry")
def retry_document_feature(
    document_id: str, feature: str, tenant: Tenant, features: Features
) -> dict[str, str]:
    if not features.reset(tenant.tenant_id, document_id, feature):
        raise HTTPException(status_code=404, detail="feature not found for this document")
    return {"status": "queued"}
