"""Entity endpoints (brief section 22). Tenant-scoped, token-protected."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from doktok_contracts.ports import (
    AuditLogRepository,
    DocumentRepository,
    EntityMergeAdjudicator,
    EntityRepository,
    KnowledgeGraphRepository,
)
from doktok_contracts.schemas import (
    AuditEventType,
    Document,
    EntitySummary,
    EntityType,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgMergeSuggestion,
    KgNeighborhood,
    KgStats,
    KgSurnameGroup,
)
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.knowledge_graph.adjudication import adjudicate_suggestions
from doktok_core.knowledge_graph.entity_resolution import (
    merge_adjudication_pair_key,
    retarget_to_cluster_root,
)
from doktok_core.knowledge_graph.predicates import canonical_edge_id
from doktok_core.knowledge_graph.resolve import canonical_entity_id
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from doktok_api.dependencies import (
    Tenant,
    get_audit_repository,
    get_document_repository,
    get_entity_merge_adjudicator,
    get_entity_repository,
    get_knowledge_graph_repository,
)

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])

Repo = Annotated[EntityRepository, Depends(get_entity_repository)]
KgRepo = Annotated[KnowledgeGraphRepository, Depends(get_knowledge_graph_repository)]
Docs = Annotated[DocumentRepository, Depends(get_document_repository)]
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

    When the pipeline model is available every non-``token_set`` suggestion (``fuzzy_trgm``,
    ``token_subset``, ``token_typo``) is adjudicated: pairs the LLM says are different
    real-world entities are dropped; surviving pairs are enriched with ``llm_*`` fields.
    ``token_set`` suggestions (certain matches) always pass through unchanged. Falls back to
    the plain deterministic list when the adjudicator is unavailable (egress blocked, no
    settings, model error).
    """
    suggestions = kg.list_merge_suggestions(tenant.tenant_id, limit=limit)
    # Drop pairs the user has already rejected so they are not re-proposed (#530). Keyed on the
    # normalized, order-independent pair key, so a rejection survives a KG rebuild. Done before
    # adjudication so a rejected fuzzy pair does not even hit the LLM.
    rejected = kg.rejected_pair_keys(tenant.tenant_id)
    if rejected:
        suggestions = [
            s
            for s in suggestions
            if merge_adjudication_pair_key(s.canonical_value, s.alias_value) not in rejected
        ]
    if adjudicator is not None:
        suggestions = adjudicate_suggestions(suggestions, kg, adjudicator, limit=limit)
    # Re-point one-hop chains at the terminal canonical AFTER adjudication (so a dropped fuzzy link
    # can't bridge a cluster): "hanja lucian" -> "lucian cosmin hanga", not "-> lucian hanga" (#566
    # follow-up).
    return retarget_to_cluster_root(suggestions)


class RejectMergeBody(BaseModel):
    canonical_value: Annotated[str, Field(min_length=1)]
    alias_value: Annotated[str, Field(min_length=1)]


@router.post("/merge-suggestions/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_merge_suggestion(
    body: RejectMergeBody,
    tenant: Tenant,
    kg: KgRepo,
    audit: Audit,
) -> None:
    """Persist a rejected merge suggestion so it is never re-proposed (#530).

    Keyed on the normalized, order-independent pair of the two entity values, so the rejection
    matches the pair regardless of direction and survives a KG rebuild. Idempotent.
    """
    pair_key = merge_adjudication_pair_key(body.canonical_value, body.alias_value)
    kg.reject_merge(tenant.tenant_id, pair_key, actor=actor_identity(tenant))
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_MERGE_REJECTED,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        description=f"merge rejected: {body.canonical_value} / {body.alias_value}",
        details={"canonical_value": body.canonical_value, "alias_value": body.alias_value},
    )


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


# Human-confirmed family link: a symmetric hint, so we store one edge in a canonical (sorted)
# direction, making a re-confirm in either direction idempotent on the same edge id.
_FAMILY_PREDICATE = "RELATED_TO"
_MANUAL_PROVENANCE_DOC = "manual"


@router.get("/family-suggestions", response_model=list[KgSurnameGroup])
def list_family_suggestions(
    tenant: Tenant,
    kg: KgRepo,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[KgSurnameGroup]:
    """Canonical PERSON nodes that share a parsed surname (#532) - a WEAK "possible family" hint,
    not a fact. The UI must render it as distinct from evidence-backed edges; only an explicit
    confirm asserts a relationship."""
    return kg.list_shared_surname_groups(tenant.tenant_id, limit=limit)


class ConfirmFamilyBody(BaseModel):
    src_id: Annotated[str, Field(min_length=1)]
    dst_id: Annotated[str, Field(min_length=1)]


@router.post(
    "/family-suggestions/confirm",
    response_model=KgEdge,
    status_code=status.HTTP_201_CREATED,
)
def confirm_family(
    body: ConfirmFamilyBody,
    tenant: Tenant,
    kg: KgRepo,
    audit: Audit,
) -> KgEdge:
    """Confirm a shared-surname pair as family: assert a manual-provenance ``RELATED_TO`` edge
    between the two PERSON nodes (#532). Mirrors the merge-queue philosophy - the weak hint only
    becomes a fact when a human confirms it. Never auto-created; never influences entity MERGE.
    """
    tid = tenant.tenant_id
    if body.src_id == body.dst_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot relate an entity to itself"
        )
    src = kg.get_entity(tid, body.src_id)
    dst = kg.get_entity(tid, body.dst_id)
    if src is None or dst is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    if src.entity_type != EntityType.PERSON or dst.entity_type != EntityType.PERSON:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="both entities must be PERSON"
        )
    # Canonicalize direction so (A,B) and (B,A) map to one edge (a family link is symmetric).
    a_id, b_id = sorted((src.id, dst.id))
    edge_id = canonical_edge_id(tid, a_id, _FAMILY_PREDICATE, b_id)
    edge = KgEdge(
        id=edge_id,
        tenant_id=tid,
        src_entity_id=a_id,
        predicate=_FAMILY_PREDICATE,
        dst_entity_id=b_id,
        metadata={"source": "manual_family"},
    )
    provenance = [
        KgEdgeProvenance(
            id=uuid4().hex,
            tenant_id=tid,
            edge_id=edge_id,
            document_id=_MANUAL_PROVENANCE_DOC,
            chunk_id=None,
            evidence=f"manual: confirmed possible family (shared surname) - "
            f"{src.normalized_value} / {dst.normalized_value}",
        )
    ]
    kg.add_edges([edge], provenance)
    record_activity(
        audit,
        tid,
        AuditEventType.ENTITY_RELATED,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        record_id=edge_id,
        description=f'"{src.normalized_value}" related to "{dst.normalized_value}" (family)',
        details={"src_id": a_id, "dst_id": b_id, "predicate": _FAMILY_PREDICATE},
    )
    stored = kg.edges_for_entity(tid, a_id)
    for candidate in stored:
        if candidate.id == edge_id:
            return candidate
    return edge


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


@router.get("/{entity_id}/documents", response_model=list[Document])
def documents_for_kg_entity(
    entity_id: str, tenant: Tenant, kg: KgRepo, docs: Docs
) -> list[Document]:
    """Documents containing a KG entity, resolved through its mentions (not by value).

    A merged/folded entity's documents mentioned an ALIAS surface form, not the canonical value, so
    a value-based lookup misses them. Going through ``kg_entity_mentions`` (which point at the
    canonical node) returns every document behind the node, aliases included.
    """
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for mention in kg.mentions_for_entity(tenant.tenant_id, entity_id):
        if mention.document_id not in seen:
            seen.add(mention.document_id)
            ordered_ids.append(mention.document_id)
    result: list[Document] = []
    for document_id in ordered_ids:
        document = docs.get(tenant.tenant_id, document_id)
        if document is not None:
            result.append(document)
    return result


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
    canonical = kg.get_entity(tenant.tenant_id, canonical_id)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="canonical entity not found"
        )
    alias = kg.get_entity(tenant.tenant_id, body.alias_id)
    if alias is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alias entity not found")
    kg.merge_entities(
        tenant.tenant_id,
        canonical_id,
        body.alias_id,
        method=body.method,
        score=body.score,
        actor=actor_identity(tenant),
    )
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_MERGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        record_id=canonical_id,
        # Human-readable: entity NAMES in the description; the ids live in details (row detail).
        description=f'"{alias.normalized_value}" merged into "{canonical.normalized_value}"',
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
    node = kg.get_entity(tenant.tenant_id, alias_id)  # capture the name before it changes
    ok = kg.split_entity(tenant.tenant_id, alias_id, actor=actor_identity(tenant))
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="entity is not a merged alias or does not exist",
        )
    name = node.normalized_value if node else alias_id
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_SPLIT,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        record_id=alias_id,
        description=f'"{name}" split from its canonical',
        details={"alias_id": alias_id},
    )
    return SplitEntityResponse(status="split")


class DecomposePart(BaseModel):
    value: Annotated[str, Field(min_length=1)]
    entity_type: EntityType


class DecomposeBody(BaseModel):
    part_a: DecomposePart  # keeps the fused node's documents + edges (same type as the source)
    part_b: DecomposePart
    predicate: Annotated[str, Field(min_length=1)] = "RELATED_TO"


@router.post("/{entity_id}/decompose", response_model=KgEntity)
def decompose_entity(
    entity_id: str, body: DecomposeBody, tenant: Tenant, kg: KgRepo, audit: Audit
) -> KgEntity:
    """Split one fused entity into two nodes + an edge (e.g. "Muenchen 222" -> "Muenchen" + "222").

    Option A: part A (same type as the source) absorbs the fused node's document mentions and edges;
    the fused node is folded into part A; part B is created/reused; a directed edge part A ->
    part B is added with the fused mention's documents as provenance. Reuses existing nodes.
    """
    fused = kg.get_entity(tenant.tenant_id, entity_id)
    if fused is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    if body.part_a.entity_type != fused.entity_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part A must keep the source entity type (it absorbs the documents/edges)",
        )
    tid = tenant.tenant_id
    a_id = canonical_entity_id(tid, body.part_a.entity_type.value, body.part_a.value)
    b_id = canonical_entity_id(tid, body.part_b.entity_type.value, body.part_b.value)
    if a_id == b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the two parts must differ"
        )
    # Create-or-reuse the two parts.
    kg.upsert_entities(
        [
            KgEntity(
                id=a_id,
                tenant_id=tid,
                entity_type=body.part_a.entity_type,
                normalized_value=body.part_a.value,
            ),
            KgEntity(
                id=b_id,
                tenant_id=tid,
                entity_type=body.part_b.entity_type,
                normalized_value=body.part_b.value,
            ),
        ]
    )
    # Fold the fused node into part A (re-points its mentions + edges), unless A IS the fused node.
    document_ids = list({m.document_id for m in kg.mentions_for_entity(tid, entity_id)})
    if a_id != entity_id:
        kg.merge_entities(tid, a_id, entity_id, method="split", actor=actor_identity(tenant))
    # Link the two parts, using the fused mention's documents as edge provenance.
    edge_id = canonical_edge_id(tid, a_id, body.predicate, b_id)
    edge = KgEdge(
        id=edge_id, tenant_id=tid, src_entity_id=a_id, predicate=body.predicate, dst_entity_id=b_id
    )
    provenance = [
        KgEdgeProvenance(
            id=uuid4().hex,
            tenant_id=tid,
            edge_id=edge_id,
            document_id=document_id,
            chunk_id=None,
            evidence=fused.normalized_value[:250],
        )
        for document_id in document_ids
    ]
    kg.add_edges([edge], provenance)
    record_activity(
        audit,
        tid,
        AuditEventType.ENTITY_SPLIT,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        record_id=a_id,
        description=(
            f'"{fused.normalized_value}" split into "{body.part_a.value}" + "{body.part_b.value}"'
        ),
        details={
            "source": fused.normalized_value,
            "part_a": body.part_a.value,
            "part_b": body.part_b.value,
            "predicate": body.predicate,
        },
    )
    part_a = kg.get_entity(tid, a_id)
    if part_a is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="part A missing after split"
        )
    return part_a


class RenameEntityBody(BaseModel):
    # Empty/blank clears the override (revert to the normalized value).
    display_name: str = ""


@router.post("/{entity_id}/rename", response_model=KgEntity)
def rename_entity(
    entity_id: str, body: RenameEntityBody, tenant: Tenant, kg: KgRepo, audit: Audit
) -> KgEntity:
    """Set a display-name override on an entity (fix an OCR'd name) - id and edges unchanged.

    Blank ``display_name`` clears the override. 404 when the entity is unknown for the tenant.
    """
    before = kg.get_entity(tenant.tenant_id, entity_id)
    if before is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="entity not found")
    kg.rename_entity(tenant.tenant_id, entity_id, body.display_name)
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.ENTITY_RENAMED,
        actor=actor_identity(tenant),
        actor_kind="user",
        record_kind="entity",
        record_id=entity_id,
        description=f'"{before.normalized_value}" renamed to "{body.display_name.strip()}"'
        if body.display_name.strip()
        else f'"{before.normalized_value}" rename cleared',
        details={"entity_id": entity_id, "display_name": body.display_name},
    )
    updated = kg.get_entity(tenant.tenant_id, entity_id)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected: entity missing after rename",
        )
    return updated
