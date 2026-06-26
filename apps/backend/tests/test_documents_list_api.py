"""Document list: sorting (acquired/created/title), token filtering (AND/OR), and the ids endpoint.

Exercises the in-memory repository (the contract oracle) through the API so the semantics the
Postgres adapter must match are pinned down without a database.
"""

import os
from datetime import UTC, date, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import CategoryRepository, DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentStatus
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.features.inmemory import InMemoryFeatureRepository
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
    unidentifiable: bool | None = None,
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
        unidentifiable=unidentifiable,
    )


def _client(
    repo: InMemoryDocumentRepository, features: InMemoryFeatureRepository | None = None
) -> TestClient:
    registry = build_registry()
    registry.register(DocumentRepository, repo)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(
        FeatureRepository,  # type: ignore[type-abstract]
        features or InMemoryFeatureRepository(),
    )
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _repo(*docs: Document) -> InMemoryDocumentRepository:
    repo = InMemoryDocumentRepository()
    for d in docs:
        repo.add(d)
    return repo


def test_unidentifiable_filter() -> None:
    flagged = _doc("u", title="Unidentifiable Document", unidentifiable=True)
    fine = _doc("a", title="Invoice", unidentifiable=False)
    unassessed = _doc("n", title="Old doc", unidentifiable=None)
    client = _client(_repo(flagged, fine, unassessed))

    only = client.get("/api/v1/documents?unidentifiable=true", headers=AUTH).json()
    assert [d["id"] for d in only["items"]] == ["u"]  # only the flagged one

    exclude = client.get("/api/v1/documents?unidentifiable=false", headers=AUTH).json()
    # excludes only the confirmed-unidentifiable; the unassessed (NULL) doc is still shown.
    assert {d["id"] for d in exclude["items"]} == {"a", "n"}

    no_filter = client.get("/api/v1/documents", headers=AUTH).json()
    assert {d["id"] for d in no_filter["items"]} == {"u", "a", "n"}
    # The flag is surfaced on the document payload for the badge.
    assert next(d for d in no_filter["items"] if d["id"] == "u")["unidentifiable"] is True


def test_title_filter_is_case_insensitive_substring() -> None:
    client = _client(
        _repo(
            _doc("a", title="Jahresrechnung M-Strom 2021"),
            _doc("b", title="Stromvertrag Anhang"),
            _doc("c", title="Invoice 2022"),
            _doc("n", title=None),
        )
    )

    # Substring, case-insensitive: matches both 'M-Strom' and 'Stromvertrag'.
    hits = client.get("/api/v1/documents?title=strom", headers=AUTH).json()
    assert {d["id"] for d in hits["items"]} == {"a", "b"} and hits["total"] == 2

    one = client.get("/api/v1/documents?title=invoice", headers=AUTH).json()
    assert [d["id"] for d in one["items"]] == ["c"]

    # No filter returns everything (including the null-title doc).
    allp = client.get("/api/v1/documents", headers=AUTH).json()
    assert {d["id"] for d in allp["items"]} == {"a", "b", "c", "n"}


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


def test_list_includes_processing_summary_sidecar() -> None:
    from datetime import timedelta

    from doktok_contracts.schemas import DocumentFeature, FeatureStatus

    doc = _doc("d1", title="Scan")
    doc.metadata = {
        "extraction_method": "ocr",
        "page_count": 2,
        "normalized_from": "application/x-docx",
    }
    features = InMemoryFeatureRepository()
    now = datetime.now(UTC)
    for i, status in enumerate([FeatureStatus.DONE, FeatureStatus.DONE, FeatureStatus.FAILED]):
        features.rows.append(
            DocumentFeature(
                id=f"f{i}",
                tenant_id="tenant-a",
                document_id="d1",
                feature=f"feat{i}",
                status=status,
                created_at=now + timedelta(seconds=i),
                updated_at=now,
            )
        )
    body = _client(_repo(doc), features).get("/api/v1/documents", headers=AUTH).json()

    # The shared Document shape is unchanged; the summary lives in the envelope sidecar map.
    assert "processing" not in body["items"][0]
    summary = body["processing"]["d1"]
    assert summary["extraction_method"] == "ocr"
    assert summary["ocr_outcome"] == "done"
    assert summary["page_count"] == 2
    assert summary["normalized_from_mime"] == "application/x-docx"
    assert summary["status"] == "active"
    assert summary["features_done"] == 2
    assert summary["features_failed"] == 1
