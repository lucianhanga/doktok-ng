import os
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, EntityRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import (
    AuditEvent,
    Document,
    DocumentEntity,
    DocumentStatus,
    EntitySummary,
    EntityType,
    EntityTypeCount,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    TokenSuggestion,
)
from doktok_core.config import Settings
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeEntityRepository:
    def add_entities(self, entities: list[DocumentEntity]) -> None: ...

    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...

    def delete_for_document_types(
        self,
        tenant_id: str,
        document_id: str,
        entity_types: list[str],
        *,
        source: str | None = None,
        keep_source: str | None = None,
    ) -> None: ...

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentEntity]:
        return []

    def mention_document_ids(
        self,
        tenant_id: str,
        term: str,
        *,
        entity_type: EntityType | None = None,
        cap: int = 10_000,
    ) -> tuple[list[str], int, bool]:
        return [], 0, False

    def suggest_tokens(
        self,
        tenant_id: str,
        prefix: str,
        *,
        selected: list[str] | None = None,
        limit: int = 10,
    ) -> list[TokenSuggestion]:
        return []

    def documents_for_tokens(
        self,
        tenant_id: str,
        tokens: list[str],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        return []

    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntitySummary]:
        items = [
            EntitySummary(
                entity_type=EntityType.EMAIL,
                normalized_value="a@b.com",
                document_count=2,
                occurrences=3,
            ),
            EntitySummary(
                entity_type=EntityType.MONEY,
                normalized_value="$50",
                document_count=1,
                occurrences=1,
            ),
        ]
        if entity_type is not None:
            items = [i for i in items if i.entity_type == entity_type]
        return items

    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Document]:
        return [
            Document(
                id="doc1",
                tenant_id=tenant_id,
                sha256="a" * 64,
                original_filename="invoice.txt",
                status=DocumentStatus.ACTIVE,
                created_at=datetime.now(UTC),
            )
        ]


def _node(node_id: str, entity_type: EntityType, value: str) -> KgEntity:
    return KgEntity(
        id=node_id, tenant_id="tenant-a", entity_type=entity_type, normalized_value=value
    )


class FakeKnowledgeGraphRepository:
    """In-memory KG for the traversal-API endpoints. Seeded: alice/bob (PERSON) + acme (ORG),
    with one edge alice --works_at--> acme; bob is an isolated node."""

    def __init__(self) -> None:
        self._nodes = {
            "e-alice": _node("e-alice", EntityType.PERSON, "alice"),
            "e-bob": _node("e-bob", EntityType.PERSON, "bob"),
            "e-acme": _node("e-acme", EntityType.ORG, "acme corp"),
        }
        self._edges = [
            KgEdge(
                id="edge-1",
                tenant_id="tenant-a",
                src_entity_id="e-alice",
                predicate="works_at",
                dst_entity_id="e-acme",
                evidence_count=2,
            )
        ]

    def list_entities_page(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KgEntity]:
        rows = sorted(self._nodes.values(), key=lambda n: n.normalized_value)
        if entity_type is not None:
            rows = [n for n in rows if n.entity_type == entity_type]
        if query is not None:
            rows = [n for n in rows if query.lower() in n.normalized_value.lower()]
        return rows[offset : offset + limit]

    def get_entity(self, tenant_id: str, entity_id: str) -> KgEntity | None:
        return self._nodes.get(entity_id)

    def get_entities(self, tenant_id: str, entity_ids: Sequence[str]) -> list[KgEntity]:
        return [self._nodes[i] for i in entity_ids if i in self._nodes]

    def entity_count(self, tenant_id: str) -> int:
        return len(self._nodes)

    def edge_count(self, tenant_id: str) -> int:
        return len(self._edges)

    def entity_type_counts(self, tenant_id: str) -> list[EntityTypeCount]:
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            counts[node.entity_type.value] = counts.get(node.entity_type.value, 0) + 1
        return [EntityTypeCount(entity_type=t, count=c) for t, c in sorted(counts.items())]

    def neighborhood(
        self,
        tenant_id: str,
        entity_ids: Sequence[str],
        *,
        hops: int = 1,
        edge_limit: int = 64,
    ) -> tuple[list[KgEdge], list[KgEdgeProvenance]]:
        seeds = set(entity_ids)
        edges = [e for e in self._edges if e.src_entity_id in seeds or e.dst_entity_id in seeds]
        return edges[:edge_limit], []


def _client() -> TestClient:
    registry = build_registry()
    registry.register(EntityRepository, FakeEntityRepository())
    registry.register(KnowledgeGraphRepository, FakeKnowledgeGraphRepository())
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_requires_token() -> None:
    assert _client().get("/api/v1/entities").status_code == 401


def test_list_entities_and_filter_by_type() -> None:
    client = _client()
    assert len(client.get("/api/v1/entities", headers=_auth()).json()) == 2
    only_email = client.get("/api/v1/entities?type=EMAIL", headers=_auth()).json()
    assert [e["entity_type"] for e in only_email] == ["EMAIL"]


def test_documents_for_entity() -> None:
    client = _client()
    rows = client.get("/api/v1/entities/documents?type=EMAIL&value=a@b.com", headers=_auth()).json()
    assert [r["original_filename"] for r in rows] == ["invoice.txt"]


# ---------------------------------------------------------------- KG traversal API (issue #465)


def test_kg_nodes_requires_token() -> None:
    assert _client().get("/api/v1/entities/nodes").status_code == 401


def test_kg_nodes_list_and_filters() -> None:
    client = _client()
    nodes = client.get("/api/v1/entities/nodes", headers=_auth()).json()
    assert [n["normalized_value"] for n in nodes] == ["acme corp", "alice", "bob"]  # ordered ASC
    persons = client.get("/api/v1/entities/nodes?type=PERSON", headers=_auth()).json()
    assert {n["normalized_value"] for n in persons} == {"alice", "bob"}
    matched = client.get("/api/v1/entities/nodes?q=corp", headers=_auth()).json()
    assert [n["normalized_value"] for n in matched] == ["acme corp"]


def test_kg_stats() -> None:
    body = _client().get("/api/v1/entities/stats", headers=_auth()).json()
    assert body["entity_count"] == 3
    assert body["edge_count"] == 1
    by_type = {c["entity_type"]: c["count"] for c in body["by_type"]}
    assert by_type == {"ORG": 1, "PERSON": 2}


def test_kg_entity_detail_and_404() -> None:
    client = _client()
    ok = client.get("/api/v1/entities/e-alice", headers=_auth())
    assert ok.status_code == 200
    assert ok.json()["normalized_value"] == "alice"
    assert client.get("/api/v1/entities/nope", headers=_auth()).status_code == 404


def test_kg_neighborhood_assembles_subgraph() -> None:
    body = _client().get("/api/v1/entities/e-alice/neighborhood", headers=_auth()).json()
    assert body["focus"]["id"] == "e-alice"
    assert {n["id"] for n in body["nodes"]} == {"e-alice", "e-acme"}
    assert len(body["edges"]) == 1
    assert body["edges"][0]["predicate"] == "works_at"


def test_kg_neighborhood_isolated_node() -> None:
    body = _client().get("/api/v1/entities/e-bob/neighborhood", headers=_auth()).json()
    assert [n["id"] for n in body["nodes"]] == ["e-bob"]
    assert body["edges"] == []


def test_kg_neighborhood_404_for_unknown_focus() -> None:
    assert _client().get("/api/v1/entities/ghost/neighborhood", headers=_auth()).status_code == 404


# -------------------------------------------------------- Merge / split / suggestions (#508)


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        return [e for e in self.events if e.tenant_id == tenant_id]


def _merge_setup() -> tuple[TestClient, InMemoryKnowledgeGraphRepository, FakeAuditLogRepository]:
    """Return a client backed by a real InMemoryKnowledgeGraphRepository seeded with:
    - e-alice / e-alice2: PERSON nodes with trigram similarity 0.69 (above 0.6 threshold)
      so list_merge_suggestions reliably proposes a merge between them.
    - e-bob: dissimilar node, not proposed for any merge.
    - e-other-tenant: belongs to tenant-b, invisible to tenant-a requests.
    """
    audit = FakeAuditLogRepository()
    kg = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            KgEntity(
                id="e-alice",
                tenant_id="tenant-a",
                entity_type=EntityType.PERSON,
                normalized_value="alice johnson",
            ),
            KgEntity(
                id="e-alice2",
                tenant_id="tenant-a",
                entity_type=EntityType.PERSON,
                normalized_value="alice jonson",
            ),
            KgEntity(
                id="e-bob",
                tenant_id="tenant-a",
                entity_type=EntityType.PERSON,
                normalized_value="bob",
            ),
            KgEntity(
                id="e-other-tenant",
                tenant_id="tenant-b",
                entity_type=EntityType.PERSON,
                normalized_value="alice johnson",
            ),
        ]
    )
    registry = build_registry()
    registry.register(EntityRepository, FakeEntityRepository())
    registry.register(KnowledgeGraphRepository, kg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))
    return client, kg, audit


def test_merge_suggestions_returns_candidates() -> None:
    client, _, _ = _merge_setup()
    r = client.get("/api/v1/entities/merge-suggestions", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    # "alice johnson" / "alice jonson": exact 'alice' + johnson~jonson one-deletion pair, so
    # the cascade proposes the pair (token_typo, #534; trigram 0.69 would also clear 0.6).
    assert len(body) >= 1
    first = body[0]
    assert "canonical_id" in first and "alias_id" in first
    assert "method" in first and "score" in first


def test_merge_suggestions_limit_capped() -> None:
    client, _, _ = _merge_setup()
    r = client.get("/api/v1/entities/merge-suggestions?limit=1", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) <= 1


def test_merge_suggestions_limit_invalid() -> None:
    client, _, _ = _merge_setup()
    r0 = client.get("/api/v1/entities/merge-suggestions?limit=0", headers=_auth())
    assert r0.status_code == 422
    r201 = client.get("/api/v1/entities/merge-suggestions?limit=201", headers=_auth())
    assert r201.status_code == 422


def test_merge_succeeds_and_alias_resolves_to_canonical() -> None:
    client, kg, audit = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-alice2", "method": "manual"},
        headers=_auth(),
    )
    assert r.status_code == 200
    body = r.json()
    # Response is the canonical KgEntity
    assert body["id"] == "e-alice"
    assert body["entity_type"] == "PERSON"
    # get_entity for the alias now chain-follows to the canonical
    merged = kg.get_entity("tenant-a", "e-alice2")
    assert merged is not None
    assert merged.id == "e-alice"
    # One entity.merged audit event recorded
    types = [e.event_type for e in audit.events]
    assert "entity.merged" in types


def test_merge_with_optional_score() -> None:
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-alice2", "method": "fuzzy_trgm", "score": 0.85},
        headers=_auth(),
    )
    assert r.status_code == 200


def test_merge_self_returns_400() -> None:
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-alice"},
        headers=_auth(),
    )
    assert r.status_code == 400


def test_merge_unknown_canonical_returns_404() -> None:
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-nope/merge",
        json={"alias_id": "e-alice"},
        headers=_auth(),
    )
    assert r.status_code == 404


def test_merge_unknown_alias_returns_404() -> None:
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-nope"},
        headers=_auth(),
    )
    assert r.status_code == 404


def test_split_reverses_merge() -> None:
    client, kg, audit = _merge_setup()
    # Merge first
    m = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-alice2"},
        headers=_auth(),
    )
    assert m.status_code == 200
    # Now split the alias back
    r = client.post("/api/v1/entities/e-alice2/split", headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "split"
    # After split, e-alice2 is its own canonical again (not chain-following to e-alice)
    node = kg.get_entity("tenant-a", "e-alice2")
    assert node is not None
    assert node.id == "e-alice2"
    # Audit trail contains both event types
    event_types = [e.event_type for e in audit.events]
    assert "entity.merged" in event_types
    assert "entity.split" in event_types


def test_split_non_alias_returns_404() -> None:
    client, _, _ = _merge_setup()
    # e-alice is a canonical node, not a merged alias
    r = client.post("/api/v1/entities/e-alice/split", headers=_auth())
    assert r.status_code == 404


def test_split_unknown_entity_returns_404() -> None:
    client, _, _ = _merge_setup()
    r = client.post("/api/v1/entities/e-ghost/split", headers=_auth())
    assert r.status_code == 404


def test_merge_tenant_isolation() -> None:
    """An entity belonging to tenant-b is invisible to tenant-a; the canonical id should 404."""
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-other-tenant/merge",
        json={"alias_id": "e-alice"},
        headers=_auth(),
    )
    assert r.status_code == 404


def test_merge_tenant_isolation_alias() -> None:
    """A tenant-b entity as alias_id should also 404 for tenant-a."""
    client, _, _ = _merge_setup()
    r = client.post(
        "/api/v1/entities/e-alice/merge",
        json={"alias_id": "e-other-tenant"},
        headers=_auth(),
    )
    assert r.status_code == 404


def test_merge_requires_token() -> None:
    client, _, _ = _merge_setup()
    assert (
        client.post("/api/v1/entities/e-alice/merge", json={"alias_id": "e-alice2"}).status_code
        == 401
    )


def test_split_requires_token() -> None:
    client, _, _ = _merge_setup()
    assert client.post("/api/v1/entities/e-alice2/split").status_code == 401


def test_merge_suggestions_requires_token() -> None:
    client, _, _ = _merge_setup()
    assert client.get("/api/v1/entities/merge-suggestions").status_code == 401
