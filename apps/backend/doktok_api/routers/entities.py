"""Entity endpoints (brief section 22). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import (
    AuditLogRepository,
    EntityMergeAdjudicator,
    EntityRepository,
    KnowledgeGraphRepository,
)
from doktok_contracts.schemas import (
    AuditEventType,
    Document,
    EntitySummary,
    EntityType,
    KgEntity,
    KgMergeSuggestion,
    KgNeighborhood,
    KgStats,
)
from doktok_core.audit.logger import record_activity
from doktok_core.knowledge_graph.adjudication import adjudicate_suggestions
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from doktok_api.dependencies import (
    Tenant,
    get_audit_repository,
    get_entity_merge_adjudicator,
    get_entity_repository,
    get_knowledge_graph_repository,
)

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])

Repo = Annotated[EntityRepository, Depends(get_entity_repository)]
KgRepo = Annotated[KnowledgeGraphRepository, Depends(get_knowledge_graph_repository)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]
OptAdjudicator = Annotated[EntityMergeAdjudicator | None, Depends(get_entity_merge_adjudicator)]


class MergeEntityBody(BaseModel):
    alias_id: Annotated[str, Field(min_length=1)]
    method: str = "manual"
    score: float | None = None


class SplitEntityResponse(BaseModel):
    status: str


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


# Static paths (/nodes, /stats, /merge-suggestions) must be declared before the dynamic
# /{entity_id} routes so that FastAPI routes these paths unambiguously.


@router.get("/merge-suggestions", response_model=list[KgMergeSuggestion])
def list_merge_suggestions(
    tenant: Tenant,
    kg: KgRepo,
    adjudicator: OptAdjudicator,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[KgMergeSuggestion]:
    """Return candidate entity merges, optionally enriched by the pipeline LLM adjudicator.

    When the pipeline model is available the ``fuzzy_trgm`` suggestions are adjudicated:
    pairs the LLM says are different real-world entities are dropped; surviving pairs are
    enriched with ``llm_*`` fields. ``token_set`` suggestions (certain matches) always pass
    through unchanged. Falls back to the plain deterministic list when the adjudicator is
    unavailable (egress blocked, no settings, model error).
    """
    suggestions = kg.list_merge_suggestions(tenant.tenant_id, limit=limit)
    if adjudicator is not None:
        suggestions = adjudicate_suggestions(suggestions, kg, adjudicator, limit=limit)
    return suggestions


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
    edges, _ = kg.neighborhood(tenant.tenant_id, [entity_id], hops=hops, edge_limit=edge_limit)
    if not edges:
        return KgNeighborhood(focus=focus, nodes=[focus], edges=[])
    node_ids: set[str] = {entity_id}
    for edge in edges:
        node_ids.add(edge.src_entity_id)
        node_ids.add(edge.dst_entity_id)
    nodes = kg.get_entities(tenant.tenant_id, list(node_ids))
    return KgNeighborhood(focus=focus, nodes=nodes, edges=edges)


@router.post("/{canonical_id}/merge", response_model=KgEntity)
def merge_entity(
    canonical_id: str,
    body: MergeEntityBody,
    tenant: Tenant,
    kg: KgRepo,
    audit: Audit,
) -> KgEntity:
    """Fold ``body.alias_id`` into ``canonical_id``.

    Returns 400 when the two ids are identical; 404 when either node is unknown for the tenant.
    The merge is idempotent: re-merging an already-merged pair re-asserts state and returns the
    canonical node. One ``entity.merged`` audit event is written per call.
    """
    if canonical_id == body.alias_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot merge an entity into itself",
        )
    if kg.get_entity(tenant.tenant_id, canonical_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="canonical entity not found"
        )
    if kg.get_entity(tenant.tenant_id, body.alias_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alias entity not found")
    kg.merge_entities(
        tenant.tenant_id,
        canonical_id,
        body.alias_id,
        method=body.method,
        score=body.score,
        actor="user",
    )
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_MERGED,
        actor="user",
        actor_kind="user",
        record_kind="entity",
        record_id=canonical_id,
        description=f"entity {body.alias_id} merged into {canonical_id}",
        details={"canonical_id": canonical_id, "alias_id": body.alias_id, "method": body.method},
    )
    result = kg.get_entity(tenant.tenant_id, canonical_id)
    if result is None:
        # Canonical existed before merge_entities; this path should not be reachable.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected: canonical node missing after merge",
        )
    return result


@router.post("/{alias_id}/split", response_model=SplitEntityResponse)
def split_entity(
    alias_id: str,
    tenant: Tenant,
    kg: KgRepo,
    audit: Audit,
) -> SplitEntityResponse:
    """Undo a merge: promote ``alias_id`` back to its own canonical node.

    Returns 404 when ``alias_id`` is unknown or is not currently a merged alias.
    One ``entity.split`` audit event is written on success.
    """
    ok = kg.split_entity(tenant.tenant_id, alias_id, actor="user")
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="entity is not a merged alias or does not exist",
        )
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_SPLIT,
        actor="user",
        actor_kind="user",
        record_kind="entity",
        record_id=alias_id,
        description=f"entity {alias_id} split from its canonical",
        details={"alias_id": alias_id},
    )
    return SplitEntityResponse(status="split")
