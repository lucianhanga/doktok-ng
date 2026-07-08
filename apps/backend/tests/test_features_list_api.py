import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import FeatureRepository
from doktok_contracts.schemas import DocumentFeature, FeatureStatus
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeFeatureRepository:
    def record_done(self, tenant_id, document_id, feature, feature_version) -> None: ...  # type: ignore[no-untyped-def]
    def ensure_for_active(self, tenant_id, features) -> int:  # type: ignore[no-untyped-def]
        return 0

    def seed_for_document(self, tenant_id, document_id, stages) -> int:  # type: ignore[no-untyped-def]
        return 0

    def claim_next(self, tenant_id, *, now, reclaim_before, dependencies=()):  # type: ignore[no-untyped-def]
        return None

    def mark_done(self, feature_id, *, feature_version, metrics=None) -> None: ...  # type: ignore[no-untyped-def]
    def mark_failed(self, feature_id, *, error, next_attempt_at) -> None: ...  # type: ignore[no-untyped-def]
    def feature_counts_for_documents(self, tenant_id, document_ids):  # type: ignore[no-untyped-def]
        return {}

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]:
        return []

    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool:
        return False

    def requeue_running(self, tenant_id: str) -> int:
        return 0

    def _rows(self, tenant_id: str) -> list[DocumentFeature]:
        now = datetime.now(UTC)
        return [
            DocumentFeature(
                id="f1",
                tenant_id=tenant_id,
                document_id="d1",
                feature="chunk_embed",
                status=FeatureStatus.DONE,
                created_at=now,
                updated_at=now,
            ),
            DocumentFeature(
                id="f2",
                tenant_id=tenant_id,
                document_id="d2",
                feature="entities",
                status=FeatureStatus.PENDING,
                created_at=now,
                updated_at=now,
            ),
        ]

    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]:
        return self._rows(tenant_id)

    def list_for_documents(self, tenant_id, document_ids):  # type: ignore[no-untyped-def]
        wanted = set(document_ids)
        return [r for r in self._rows(tenant_id) if r.document_id in wanted]


def _client() -> TestClient:
    registry = build_registry()
    registry.register(FeatureRepository, FakeFeatureRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_requires_token() -> None:
    assert _client().get("/api/v1/features").status_code == 401


def test_lists_tenant_feature_rows() -> None:
    body = _client().get("/api/v1/features", headers={"Authorization": "Bearer tok-a"}).json()
    assert {(r["document_id"], r["feature"], r["status"]) for r in body} == {
        ("d1", "chunk_embed", "done"),
        ("d2", "entities", "pending"),
    }


def test_scopes_to_requested_document_ids() -> None:
    # The list view passes the visible document ids so badges cover exactly those (no row cap).
    body = (
        _client()
        .get("/api/v1/features?document_ids=d1", headers={"Authorization": "Bearer tok-a"})
        .json()
    )
    assert {r["document_id"] for r in body} == {"d1"}


def test_empty_document_ids_returns_nothing() -> None:
    body = (
        _client()
        .get("/api/v1/features?document_ids=", headers={"Authorization": "Bearer tok-a"})
        .json()
    )
    assert body == []


def test_catalog_requires_token() -> None:
    assert _client().get("/api/v1/features/catalog").status_code == 401


def test_catalog_lists_reprocessable_features() -> None:
    body = (
        _client().get("/api/v1/features/catalog", headers={"Authorization": "Bearer tok-a"}).json()
    )
    names = {entry["name"] for entry in body}
    # The reprocessable features (each backed by a reconciler processor); never inline "extract".
    assert names == {
        "chunk_embed",
        "entities",
        "ner",
        "entity_graph",
        "relations",
        "doc_metadata",
        "doc_classify",
        "structured_records",
        "thumbnail",
    }
    assert "extract" not in names
    assert all(entry["label"] and entry["description"] for entry in body)


# --- GET /api/v1/features/groups ---


def test_groups_requires_token() -> None:
    assert _client().get("/api/v1/features/groups").status_code == 401


def test_groups_returns_exactly_two_groups() -> None:
    body = (
        _client().get("/api/v1/features/groups", headers={"Authorization": "Bearer tok-a"}).json()
    )
    assert len(body) == 2
    assert {entry["id"] for entry in body} == {"entities", "knowledge_graph"}


def test_groups_each_entry_has_required_fields() -> None:
    body = (
        _client().get("/api/v1/features/groups", headers={"Authorization": "Bearer tok-a"}).json()
    )
    for entry in body:
        assert entry["id"]
        assert entry["label"]
        assert isinstance(entry["badge_members"], list)
        assert len(entry["badge_members"]) > 0


def test_groups_entities_badge_members() -> None:
    body = (
        _client().get("/api/v1/features/groups", headers={"Authorization": "Bearer tok-a"}).json()
    )
    entities = next(e for e in body if e["id"] == "entities")
    assert entities["label"] == "Entities"
    assert set(entities["badge_members"]) == {"entities", "ner"}


def test_groups_knowledge_graph_badge_members() -> None:
    body = (
        _client().get("/api/v1/features/groups", headers={"Authorization": "Bearer tok-a"}).json()
    )
    kg = next(e for e in body if e["id"] == "knowledge_graph")
    assert kg["label"] == "Knowledge graph"
    assert set(kg["badge_members"]) == {"entity_graph", "relations"}


def test_groups_expose_reprocess_set_with_the_entities_auto_chain() -> None:
    """reprocess_set is exposed so a per-document group reprocess chains the same features as the
    all-docs action. Entities auto-chains the graph (entity_graph + relations); knowledge_graph does
    not widen beyond its own two features."""
    body = (
        _client().get("/api/v1/features/groups", headers={"Authorization": "Bearer tok-a"}).json()
    )
    entities = next(e for e in body if e["id"] == "entities")
    assert entities["reprocess_set"] == ["entities", "ner", "entity_graph", "relations"]
    kg = next(e for e in body if e["id"] == "knowledge_graph")
    assert kg["reprocess_set"] == ["entity_graph", "relations"]
