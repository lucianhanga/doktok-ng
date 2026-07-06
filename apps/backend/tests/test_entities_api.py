import os
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import EntityRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import (
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
        self, tenant_id: str, document_id: str, entity_types: list[str]
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
    registry.register(EntityRepository, FakeEntityRepository())  # type: ignore[type-abstract]
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
