"""Document list: sorting (acquired/created/title), token filtering (AND/OR), and the ids endpoint.

Exercises the in-memory repository (the contract oracle) through the API so the semantics the
Postgres adapter must match are pinned down without a database.
"""

import os
from datetime import UTC, date, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import CategoryRepository, DocumentRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _doc(
    doc_id: str,
    *,
    title: str | None = None,
    document_date: date | None = None,
    created_at: datetime | None = None,
) -> Document:
    return Document(
        id=doc_id,
        tenant_id="tenant-a",
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.txt",
        title=title,
        document_date=document_date,
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs.active/{doc_id}",
        created_at=created_at or datetime.now(UTC),
    )


def _client(repo: InMemoryDocumentRepository) -> TestClient:
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _repo(*docs: Document) -> InMemoryDocumentRepository:
    repo = InMemoryDocumentRepository()
    for d in docs:
        repo.add(d)
    return repo


def test_sort_by_created_date_desc_nulls_last() -> None:
    a = _doc("a", document_date=date(2024, 1, 10))
    b = _doc("b", document_date=date(2024, 3, 1))
    c = _doc("c", document_date=None)  # no document date -> sorts last in both directions
    client = _client(_repo(a, b, c))
    body = client.get("/api/v1/documents?sort=created&dir=desc", headers=AUTH).json()
    assert [d["id"] for d in body["items"]] == ["b", "a", "c"]
    asc = client.get("/api/v1/documents?sort=created&dir=asc", headers=AUTH).json()
    assert [d["id"] for d in asc["items"]] == ["a", "b", "c"]  # null still last


def test_sort_by_title_paginates_with_cursor() -> None:
    docs = [_doc(f"d{i}", title=t) for i, t in enumerate(["Zeta", "Alpha", "Mango"])]
    client = _client(_repo(*docs))
    p1 = client.get("/api/v1/documents?sort=title&dir=asc&limit=2", headers=AUTH).json()
    assert [d["title"] for d in p1["items"]] == ["Alpha", "Mango"]
    assert p1["next_cursor"]
    p2 = client.get(
        f"/api/v1/documents?sort=title&dir=asc&limit=2&cursor={p1['next_cursor']}", headers=AUTH
    ).json()
    assert [d["title"] for d in p2["items"]] == ["Zeta"]
    assert p2["next_cursor"] is None


def test_cursor_rejected_when_sort_changes() -> None:
    docs = [_doc(f"d{i}", title=t) for i, t in enumerate(["b", "a", "c"])]
    client = _client(_repo(*docs))
    p1 = client.get("/api/v1/documents?sort=title&limit=1", headers=AUTH).json()
    # Replaying a title cursor against an acquired sort must 400, not silently mis-page.
    resp = client.get(f"/api/v1/documents?sort=acquired&cursor={p1['next_cursor']}", headers=AUTH)
    assert resp.status_code == 400


def test_token_filter_all_and_any() -> None:
    repo = _repo(_doc("d1"), _doc("d2"), _doc("d3"))
    repo.tokens_by_doc = {
        "d1": {("ORG", "Acme"), ("PERSON", "Bob")},
        "d2": {("ORG", "Acme")},
        "d3": {("PERSON", "Bob")},
    }
    client = _client(repo)

    # AND (default): only documents carrying *both* tokens.
    both = client.get("/api/v1/documents?token=Acme&token=Bob", headers=AUTH).json()
    assert {d["id"] for d in both["items"]} == {"d1"} and both["total"] == 1

    # OR: any of the tokens.
    either = client.get(
        "/api/v1/documents?token=Acme&token=Bob&token_match=any", headers=AUTH
    ).json()
    assert {d["id"] for d in either["items"]} == {"d1", "d2", "d3"}

    # Constrained to an entity type: "Bob" only as a PERSON.
    typed = client.get("/api/v1/documents?token=Bob&token_type=PERSON", headers=AUTH).json()
    assert {d["id"] for d in typed["items"]} == {"d1", "d3"}


def test_ids_endpoint_returns_all_matching_ids() -> None:
    repo = _repo(_doc("d1"), _doc("d2"), _doc("d3"))
    repo.tokens_by_doc = {"d1": {("ORG", "Acme")}, "d2": {("ORG", "Acme")}}
    body = _client(repo).get("/api/v1/documents/ids?token=Acme", headers=AUTH).json()
    assert sorted(body["ids"]) == ["d1", "d2"]
    assert body["total"] == 2 and body["truncated"] is False


def test_ids_requires_token_auth() -> None:
    assert _client(_repo(_doc("d1"))).get("/api/v1/documents/ids").status_code == 401


def test_too_many_tokens_is_400() -> None:
    client = _client(_repo(_doc("d1")))
    qs = "&".join(f"token=t{i}" for i in range(21))
    assert client.get(f"/api/v1/documents?{qs}", headers=AUTH).status_code == 400
