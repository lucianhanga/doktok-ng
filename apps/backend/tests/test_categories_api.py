"""Category co-occurrence endpoint (GET /api/v1/categories/co-occurrence)."""

from __future__ import annotations

import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import CategoryRepository
from doktok_core.categories.inmemory import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}
TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
AUTH_A = {"Authorization": "Bearer tok-a"}
AUTH_B = {"Authorization": "Bearer tok-b"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(cats: InMemoryCategoryRepository) -> TestClient:
    registry = build_registry()
    registry.register(CategoryRepository, cats)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _setup_repo() -> InMemoryCategoryRepository:
    """Three docs: A+B share 2, A+C share 1, B+C share 1. D is solo (no pairs).

    Tenant A:
      doc1: Alpha, Beta, Gamma
      doc2: Alpha, Beta
      doc3: Alpha, Gamma
      doc4: Beta            (single-category, contributes no pair)

    Tenant B:
      docX: X, Y            (completely separate)
    """
    cats = InMemoryCategoryRepository()
    a = cats.create(TENANT_A, "Alpha", "alpha")
    b = cats.create(TENANT_A, "Beta", "beta")
    c = cats.create(TENANT_A, "Gamma", "gamma")
    d = cats.create(TENANT_A, "Delta", "delta")
    assert a and b and c and d
    cats.set_document_categories(TENANT_A, "doc1", [a.id, b.id, c.id])
    cats.set_document_categories(TENANT_A, "doc2", [a.id, b.id])
    cats.set_document_categories(TENANT_A, "doc3", [a.id, c.id])
    cats.set_document_categories(TENANT_A, "doc4", [b.id])
    x = cats.create(TENANT_B, "X", "x")
    y = cats.create(TENANT_B, "Y", "y")
    assert x and y
    cats.set_document_categories(TENANT_B, "docX", [x.id, y.id])
    return cats


def test_co_occurrence_returns_pair_counts() -> None:
    client = _client(_setup_repo())
    resp = client.get("/api/v1/categories/co-occurrence", headers=AUTH_A)
    assert resp.status_code == 200
    data = resp.json()
    # Use frozenset keys: a_name/b_name order is driven by UUID string comparison, not name order.
    pairs = {frozenset([r["a_name"], r["b_name"]]): r["count"] for r in data}
    # A+B: doc1, doc2 -> 2
    assert pairs[frozenset(["Alpha", "Beta"])] == 2
    # A+C: doc1, doc3 -> 2
    assert pairs[frozenset(["Alpha", "Gamma"])] == 2
    # B+C: doc1 only -> 1
    assert pairs[frozenset(["Beta", "Gamma"])] == 1
    # Delta is solo - not in any pair
    assert all("Delta" not in (r["a_name"], r["b_name"]) for r in data)


def test_co_occurrence_ordered_by_count_desc() -> None:
    client = _client(_setup_repo())
    resp = client.get("/api/v1/categories/co-occurrence", headers=AUTH_A)
    assert resp.status_code == 200
    counts = [r["count"] for r in resp.json()]
    assert counts == sorted(counts, reverse=True)


def test_co_occurrence_tenant_isolation() -> None:
    client = _client(_setup_repo())
    resp_a = client.get("/api/v1/categories/co-occurrence", headers=AUTH_A)
    resp_b = client.get("/api/v1/categories/co-occurrence", headers=AUTH_B)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    a_names = {frozenset([r["a_name"], r["b_name"]]) for r in resp_a.json()}
    b_names = {frozenset([r["a_name"], r["b_name"]]) for r in resp_b.json()}
    # Tenant A sees Alpha/Beta/Gamma pairs; tenant B sees only X/Y
    assert not a_names.intersection(b_names)
    assert frozenset(["X", "Y"]) in b_names


def test_co_occurrence_empty_when_no_pairs() -> None:
    cats = InMemoryCategoryRepository()
    a = cats.create(TENANT_A, "Lonely", "lonely")
    assert a
    cats.set_document_categories(TENANT_A, "docL", [a.id])
    client = _client(cats)
    resp = client.get("/api/v1/categories/co-occurrence", headers=AUTH_A)
    assert resp.status_code == 200
    assert resp.json() == []


def test_co_occurrence_requires_auth() -> None:
    client = _client(_setup_repo())
    resp = client.get("/api/v1/categories/co-occurrence")
    assert resp.status_code == 401


def test_co_occurrence_response_schema() -> None:
    client = _client(_setup_repo())
    resp = client.get("/api/v1/categories/co-occurrence", headers=AUTH_A)
    assert resp.status_code == 200
    for item in resp.json():
        assert set(item.keys()) == {"a_id", "a_name", "b_id", "b_name", "count"}
        assert isinstance(item["a_id"], str)
        assert isinstance(item["a_name"], str)
        assert isinstance(item["b_id"], str)
        assert isinstance(item["b_name"], str)
        assert isinstance(item["count"], int)
        assert item["count"] > 0
