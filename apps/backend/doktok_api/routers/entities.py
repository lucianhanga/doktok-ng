"""Entity endpoints (brief section 22). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import EntityRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import (
    Document,
    EntitySummary,
    EntityType,
    KgEntity,
    KgNeighborhood,
    KgStats,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status

from doktok_api.dependencies import Tenant, get_entity_repository, get_knowledge_graph_repository

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])

Repo = Annotated[EntityRepository, Depends(get_entity_repository)]
KgRepo = Annotated[KnowledgeGraphRepository, Depends(get_knowledge_graph_repository)]


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


# Static paths (/nodes, /stats) must be declared before the dynamic /{entity_id} routes so that
# FastAPI routes these paths unambiguously.


@router.get("/nodes", response_model=list[KgEntity])
def list_kg_nodes(
    tenant: Tenant,
    kg: KgRepo,
    entity_type: Annotated[EntityType | None, Query(alias="type")] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[KgEntity]:
    return kg.list_entities_page(
        tenant.tenant_id, entity_type=entity_type, query=q, limit=limit, offset=offset
    )


@router.get("/stats", response_model=KgStats)
def kg_stats(tenant: Tenant, kg: KgRepo) -> KgStats:
    return KgStats(
        entity_count=kg.entity_count(tenant.tenant_id),
        edge_count=kg.edge_count(tenant.tenant_id),
        by_type=kg.entity_type_counts(tenant.tenant_id),
    )


@router.get("/{entity_id}", response_model=KgEntity)
def get_kg_entity(tenant: Tenant, kg: KgRepo, entity_id: str) -> KgEntity:
    node = kg.get_entity(tenant.tenant_id, entity_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    return node


@router.get("/{entity_id}/neighborhood", response_model=KgNeighborhood)
def get_kg_neighborhood(
    tenant: Tenant,
    kg: KgRepo,
    entity_id: str,
    hops: Annotated[int, Query(ge=1, le=3)] = 1,
    edge_limit: Annotated[int, Query(ge=1, le=500)] = 64,
) -> KgNeighborhood:
    focus = kg.get_entity(tenant.tenant_id, entity_id)
    if focus is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    edges, _ = kg.neighborhood(
        tenant.tenant_id, [entity_id], hops=hops, edge_limit=edge_limit
    )
    if not edges:
        return KgNeighborhood(focus=focus, nodes=[focus], edges=[])
    node_ids: set[str] = {entity_id}
    for edge in edges:
        node_ids.add(edge.src_entity_id)
        node_ids.add(edge.dst_entity_id)
    nodes = kg.get_entities(tenant.tenant_id, list(node_ids))
    return KgNeighborhood(focus=focus, nodes=nodes, edges=edges)
