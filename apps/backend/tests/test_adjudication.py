"""Tests for the LLM adjudication layer over entity merge suggestions (#510).

All tests use a FAKE adjudicator (no real model call):
- A "different" verdict drops a fuzzy_trgm candidate.
- A "same" verdict enriches the suggestion with llm_* fields.
- token_set suggestions bypass the LLM entirely.
- A raising/unavailable adjudicator falls back to the deterministic suggestions unchanged.

The core service (``adjudicate_suggestions``) is also tested end-to-end via HTTP using a
FakeAdjudicator injected into the DI registry.
"""

from __future__ import annotations

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, EntityRepository, KnowledgeGraphRepository
from doktok_contracts.schemas import (
    EntityProfile,
    EntityType,
    KgEdge,
    KgEdgeProvenance,
    KgEntity,
    KgMergeSuggestion,
    MergeVerdict,
    TokenSuggestion,
)
from doktok_core.config import Settings
from doktok_core.knowledge_graph.adjudication import adjudicate_suggestions, build_entity_profile
from doktok_core.knowledge_graph.entity_resolution import METHOD_FUZZY_TRGM, METHOD_TOKEN_SET
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
TENANT = "tenant-a"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Fake adjudicators
# ---------------------------------------------------------------------------


class FakeSameAdjudicator:
    """Always says the entities are the same, with high confidence."""

    def __init__(self, *, canonical: str = "") -> None:
        self._canonical = canonical

    def adjudicate(self, a: EntityProfile, b: EntityProfile) -> MergeVerdict:
        return MergeVerdict(
            same=True,
            canonical=self._canonical or a.normalized_value,
            confidence=0.95,
            reason="fake: same entity",
        )


class FakeDifferentAdjudicator:
    """Always says the entities are different."""

    def adjudicate(self, a: EntityProfile, b: EntityProfile) -> MergeVerdict:
        return MergeVerdict(
            same=False,
            canonical=a.normalized_value,
            confidence=0.9,
            reason="fake: different entities",
        )


class FakeRaisingAdjudicator:
    """Always raises, simulating a model error or unavailability."""

    def adjudicate(self, a: EntityProfile, b: EntityProfile) -> MergeVerdict:
        raise RuntimeError("fake model error")


# ---------------------------------------------------------------------------
# Helper: a KG repo with two PERSON nodes (fuzzy match) and edges
# ---------------------------------------------------------------------------


def _kg_with_persons() -> InMemoryKnowledgeGraphRepository:
    kg: InMemoryKnowledgeGraphRepository = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            KgEntity(
                id="e-alice",
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="alice johnson",
            ),
            KgEntity(
                id="e-alice2",
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="alice jonson",
            ),
            KgEntity(
                id="e-acme",
                tenant_id=TENANT,
                entity_type=EntityType.ORG,
                normalized_value="acme corp",
            ),
        ]
    )
    return kg


def _token_set_suggestion() -> KgMergeSuggestion:
    return KgMergeSuggestion(
        tenant_id=TENANT,
        entity_type=EntityType.PERSON,
        canonical_id="e-alice",
        canonical_value="alice johnson",
        alias_id="e-alias",
        alias_value="johnson alice",
        method=METHOD_TOKEN_SET,
        score=1.0,
    )


def _fuzzy_suggestion(
    canonical_id: str = "e-alice",
    canonical_value: str = "alice johnson",
    alias_id: str = "e-alice2",
    alias_value: str = "alice jonson",
) -> KgMergeSuggestion:
    return KgMergeSuggestion(
        tenant_id=TENANT,
        entity_type=EntityType.PERSON,
        canonical_id=canonical_id,
        canonical_value=canonical_value,
        alias_id=alias_id,
        alias_value=alias_value,
        method=METHOD_FUZZY_TRGM,
        score=0.69,
    )


# ---------------------------------------------------------------------------
# Unit tests for adjudicate_suggestions (core service)
# ---------------------------------------------------------------------------


class TestAdjudicateSuggestions:
    def test_token_set_bypasses_llm(self) -> None:
        """token_set suggestions pass through with no LLM call, even for a raising adjudicator."""
        kg = _kg_with_persons()
        suggestion = _token_set_suggestion()
        result = adjudicate_suggestions([suggestion], kg, FakeRaisingAdjudicator())
        assert len(result) == 1
        assert result[0].method == METHOD_TOKEN_SET
        # LLM fields must be None - the adjudicator was never called
        assert result[0].llm_same is None
        assert result[0].llm_confidence is None
        assert result[0].llm_reason is None

    def test_same_verdict_enriches_fuzzy_suggestion(self) -> None:
        """A 'same' verdict keeps the suggestion and populates llm_* fields."""
        kg = _kg_with_persons()
        suggestion = _fuzzy_suggestion()
        result = adjudicate_suggestions([suggestion], kg, FakeSameAdjudicator())
        assert len(result) == 1
        enriched = result[0]
        assert enriched.method == METHOD_FUZZY_TRGM
        assert enriched.llm_same is True
        assert enriched.llm_confidence == pytest.approx(0.95)
        assert enriched.llm_reason == "fake: same entity"
        assert enriched.llm_canonical is not None

    def test_different_verdict_drops_fuzzy_suggestion(self) -> None:
        """A 'different' verdict removes the suggestion from the queue."""
        kg = _kg_with_persons()
        suggestion = _fuzzy_suggestion()
        result = adjudicate_suggestions([suggestion], kg, FakeDifferentAdjudicator())
        assert result == []

    def test_raising_adjudicator_falls_back_to_original(self) -> None:
        """When the adjudicator raises, the original suggestion is kept unchanged."""
        kg = _kg_with_persons()
        suggestion = _fuzzy_suggestion()
        result = adjudicate_suggestions([suggestion], kg, FakeRaisingAdjudicator())
        assert len(result) == 1
        # Suggestion is unchanged (no llm_* fields set, original canonical direction kept)
        assert result[0].llm_same is None
        assert result[0].canonical_id == suggestion.canonical_id
        assert result[0].alias_id == suggestion.alias_id

    def test_mixed_suggestions_token_set_not_adjudicated(self) -> None:
        """token_set passes through, fuzzy is adjudicated correctly in a mixed list."""
        kg = _kg_with_persons()
        ts = _token_set_suggestion()
        fz = _fuzzy_suggestion()
        result = adjudicate_suggestions([ts, fz], kg, FakeSameAdjudicator())
        assert len(result) == 2
        assert result[0].method == METHOD_TOKEN_SET
        assert result[0].llm_same is None  # token_set: no LLM call
        assert result[1].method == METHOD_FUZZY_TRGM
        assert result[1].llm_same is True

    def test_different_verdict_drops_only_fuzzy_keeps_token_set(self) -> None:
        """Drop fuzzy, keep token_set even when adjudicator says different for fuzzy."""
        kg = _kg_with_persons()
        ts = _token_set_suggestion()
        fz = _fuzzy_suggestion()
        result = adjudicate_suggestions([ts, fz], kg, FakeDifferentAdjudicator())
        assert len(result) == 1
        assert result[0].method == METHOD_TOKEN_SET

    def test_limit_caps_adjudicator_calls(self) -> None:
        """Only the first ``limit`` suggestions are processed."""
        kg = _kg_with_persons()
        fz1 = _fuzzy_suggestion()
        fz2 = KgMergeSuggestion(
            tenant_id=TENANT,
            entity_type=EntityType.PERSON,
            canonical_id="e-alice",
            canonical_value="alice johnson",
            alias_id="e-alice3",
            alias_value="alice john",
            method=METHOD_FUZZY_TRGM,
            score=0.65,
        )
        # With limit=1 only the first is adjudicated; the second is silently truncated
        result = adjudicate_suggestions([fz1, fz2], kg, FakeSameAdjudicator(), limit=1)
        assert len(result) == 1
        assert result[0].canonical_id == "e-alice"

    def test_same_verdict_with_alias_canonical_flips_direction(self) -> None:
        """When the LLM prefers the alias value as canonical, the direction is swapped."""
        kg = _kg_with_persons()
        # Deterministic cascade made 'alice johnson' canonical and 'alice jonson' the alias.
        # LLM says 'alice jonson' should be canonical (fake adjudicator returns the alias value).
        fz = _fuzzy_suggestion(
            canonical_id="e-alice",
            canonical_value="alice johnson",
            alias_id="e-alice2",
            alias_value="alice jonson",
        )
        adjudicator = FakeSameAdjudicator(canonical="alice jonson")  # prefers alias
        result = adjudicate_suggestions([fz], kg, adjudicator)
        assert len(result) == 1
        flipped = result[0]
        # Direction should be flipped: the alias is now canonical
        assert flipped.canonical_id == "e-alice2"
        assert flipped.canonical_value == "alice jonson"
        assert flipped.alias_id == "e-alice"
        assert flipped.alias_value == "alice johnson"
        # LLM fields are still set
        assert flipped.llm_same is True
        assert flipped.llm_canonical == "alice jonson"


# ---------------------------------------------------------------------------
# Unit tests for build_entity_profile
# ---------------------------------------------------------------------------


class TestBuildEntityProfile:
    def test_profile_with_no_edges_has_empty_neighbors(self) -> None:
        kg = _kg_with_persons()
        profile = build_entity_profile(TENANT, "e-alice", "alice johnson", "PERSON", kg)
        assert profile.entity_id == "e-alice"
        assert profile.normalized_value == "alice johnson"
        assert profile.entity_type == "PERSON"
        assert profile.neighbors == []

    def test_profile_includes_neighbor_edges(self) -> None:
        kg = _kg_with_persons()
        # Add an edge: alice --works_at--> acme corp
        kg.replace_edges_for_document(
            TENANT,
            "doc-1",
            edges=[
                KgEdge(
                    id="edge-1",
                    tenant_id=TENANT,
                    src_entity_id="e-alice",
                    predicate="works_at",
                    dst_entity_id="e-acme",
                    evidence_count=1,
                )
            ],
            provenance=[
                KgEdgeProvenance(
                    id="prov-1",
                    tenant_id=TENANT,
                    edge_id="edge-1",
                    document_id="doc-1",
                    evidence="alice johnson works at acme corp",
                )
            ],
        )
        profile = build_entity_profile(TENANT, "e-alice", "alice johnson", "PERSON", kg)
        assert len(profile.neighbors) == 1
        assert "works_at" in profile.neighbors[0]
        assert "acme corp" in profile.neighbors[0]
        assert "ORG" in profile.neighbors[0]


# ---------------------------------------------------------------------------
# HTTP integration tests: adjudicator injected into the registry
# ---------------------------------------------------------------------------


class FakeEntityRepository:
    def add_entities(self, entities: object) -> None: ...
    def delete_for_document(self, tenant_id: str, document_id: str) -> None: ...
    def delete_for_document_types(
        self, tenant_id: str, document_id: str, entity_types: list[str]
    ) -> None: ...
    def list_for_document(self, tenant_id: str, document_id: str) -> list[object]:
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
        self, tenant_id: str, prefix: str, *, selected: list[str] | None = None, limit: int = 10
    ) -> list[TokenSuggestion]:
        return []

    def documents_for_tokens(
        self, tenant_id: str, tokens: list[str], *, limit: int = 50, offset: int = 0
    ) -> list[object]:
        return []

    def list_distinct(
        self,
        tenant_id: str,
        *,
        entity_type: EntityType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[object]:
        return []

    def documents_for_entity(
        self,
        tenant_id: str,
        entity_type: EntityType,
        normalized_value: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[object]:
        return []

    def entity_counts_for_documents(
        self, tenant_id: str, document_ids: list[str]
    ) -> dict[str, int]:
        return {}


class FakeAuditLogRepository:
    def record(self, event: object) -> None: ...
    def list_events(
        self,
        tenant_id: str,
        *,
        document_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[object]:
        return []


def _adjudication_client(
    kg: InMemoryKnowledgeGraphRepository,
    adjudicator: object | None = None,
) -> TestClient:
    registry = build_registry()
    registry.register(EntityRepository, FakeEntityRepository())
    registry.register(KnowledgeGraphRepository, kg)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, FakeAuditLogRepository())
    if adjudicator is not None:
        from doktok_contracts.ports import EntityMergeAdjudicator

        registry.register(EntityMergeAdjudicator, adjudicator)
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def _seeded_kg() -> InMemoryKnowledgeGraphRepository:
    """KG seeded with alice johnson / alice jonson (fuzzy match above 0.6 threshold)."""
    kg: InMemoryKnowledgeGraphRepository = InMemoryKnowledgeGraphRepository()
    kg.upsert_entities(
        [
            KgEntity(
                id="e-alice",
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="alice johnson",
            ),
            KgEntity(
                id="e-alice2",
                tenant_id=TENANT,
                entity_type=EntityType.PERSON,
                normalized_value="alice jonson",
            ),
        ]
    )
    return kg


class TestMergeSuggestionsEndpointWithAdjudicator:
    def test_no_adjudicator_returns_deterministic_suggestions(self) -> None:
        """Without an adjudicator the endpoint returns the plain deterministic list."""
        client = _adjudication_client(_seeded_kg(), adjudicator=None)
        r = client.get("/api/v1/entities/merge-suggestions", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert len(body) >= 1
        # No llm_* enrichment
        assert body[0]["llm_same"] is None

    def test_same_adjudicator_enriches_response(self) -> None:
        """When adjudicator says same, the response includes llm_* fields."""
        client = _adjudication_client(_seeded_kg(), adjudicator=FakeSameAdjudicator())
        r = client.get("/api/v1/entities/merge-suggestions", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        assert len(body) >= 1
        fuzzy = next((s for s in body if s["method"] == METHOD_FUZZY_TRGM), None)
        assert fuzzy is not None
        assert fuzzy["llm_same"] is True
        assert fuzzy["llm_confidence"] == pytest.approx(0.95)
        assert fuzzy["llm_reason"] is not None

    def test_different_adjudicator_drops_fuzzy_suggestions(self) -> None:
        """When adjudicator says different, fuzzy suggestions are removed from the list."""
        client = _adjudication_client(_seeded_kg(), adjudicator=FakeDifferentAdjudicator())
        r = client.get("/api/v1/entities/merge-suggestions", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        # All fuzzy suggestions should be gone
        fuzzy = [s for s in body if s["method"] == METHOD_FUZZY_TRGM]
        assert fuzzy == []

    def test_raising_adjudicator_falls_back_to_deterministic(self) -> None:
        """When the adjudicator errors, the endpoint falls back to the deterministic list."""
        client = _adjudication_client(_seeded_kg(), adjudicator=FakeRaisingAdjudicator())
        r = client.get("/api/v1/entities/merge-suggestions", headers=_auth())
        assert r.status_code == 200
        body = r.json()
        # Suggestions are present (not dropped) and not enriched
        assert len(body) >= 1
        assert body[0]["llm_same"] is None
