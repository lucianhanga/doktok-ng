import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import (
    CategoryRepository,
    ChunkRepository,
    DocumentRepository,
    EntityRepository,
)
from doktok_contracts.schemas import (
    Document,
    DocumentChunk,
    DocumentEntity,
    DocumentStatus,
    EntityType,
    ExtractedRecord,
)
from doktok_core.categories import InMemoryCategoryRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.indexing.inmemory import InMemoryChunkRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(
    storage_path: str,
    *,
    cats: InMemoryCategoryRepository | None = None,
    chunks: InMemoryChunkRepository | None = None,
    more_docs: list[Document] | None = None,
) -> TestClient:
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="note.txt",
        detected_mime="text/plain",
        title="note",
        status=DocumentStatus.ACTIVE,
        storage_path=storage_path,
        created_at=datetime.now(UTC),
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    for extra in more_docs or []:
        doc_repo.add(extra)
    entity_repo = InMemoryEntityRepository()
    entity_repo.add_entities(
        [
            DocumentEntity(
                id="e1",
                tenant_id="tenant-a",
                document_id="d1",
                version_id="",
                entity_text="a@b.com",
                entity_type=EntityType.EMAIL,
                normalized_value="a@b.com",
                frequency=1,
            )
        ]
    )
    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, entity_repo)  # type: ignore[type-abstract]
    if cats is not None:
        registry.register(CategoryRepository, cats)  # type: ignore[type-abstract]
    if chunks is not None:
        registry.register(ChunkRepository, chunks)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_content_reads_content_md(tmp_path: Path) -> None:
    (tmp_path / "content.md").write_text("hello body text", encoding="utf-8")
    client = _client(str(tmp_path))
    body = client.get("/api/v1/documents/d1/content", headers=_auth()).json()
    assert body == {"document_id": "d1", "content": "hello body text"}


def test_content_other_tenant_is_404(tmp_path: Path) -> None:
    client = _client(str(tmp_path))
    # No such document for tenant-a id 'missing'
    assert client.get("/api/v1/documents/missing/content", headers=_auth()).status_code == 404


def test_document_entities(tmp_path: Path) -> None:
    client = _client(str(tmp_path))
    rows = client.get("/api/v1/documents/d1/entities", headers=_auth()).json()
    assert [r["normalized_value"] for r in rows] == ["a@b.com"]


def _detail_client(tmp_path: Path) -> TestClient:
    from doktok_contracts.ports import (
        AuditLogRepository,
        CategoryRepository,
        FeatureRepository,
        RecordRepository,
    )
    from doktok_core.aggregation.inmemory import InMemoryRecordRepository
    from doktok_core.audit.inmemory import InMemoryAuditLogRepository
    from doktok_core.categories.inmemory import InMemoryCategoryRepository
    from doktok_core.features.inmemory import InMemoryFeatureRepository

    (tmp_path / "content.md").write_text("x" * 9000, encoding="utf-8")  # longer than the excerpt
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="note.txt",
        title="note",
        status=DocumentStatus.ACTIVE,
        storage_path=str(tmp_path),
        created_at=datetime.now(UTC),
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    entity_repo = InMemoryEntityRepository()
    entity_repo.add_entities(
        [
            DocumentEntity(
                id=f"e{i}",
                tenant_id="tenant-a",
                document_id="d1",
                version_id="",
                entity_text=f"v{i}",
                entity_type=EntityType.EMAIL if i % 2 else EntityType.DATE,
                normalized_value=f"v{i}",
                frequency=i,
            )
            for i in range(1, 6)
        ]
    )
    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, entity_repo)  # type: ignore[type-abstract]
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(RecordRepository, InMemoryRecordRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def test_detail_aggregate(tmp_path: Path) -> None:
    body = _detail_client(tmp_path).get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert body["document"]["id"] == "d1"
    # Entity summary: total + by-type counts + top-by-frequency (not the full list).
    assert body["entities"]["total"] == 5
    assert {b["entity_type"]: b["count"] for b in body["entities"]["by_type"]} == {
        "EMAIL": 3,
        "DATE": 2,
    }
    assert body["entities"]["top"][0]["frequency"] == 5  # highest first
    # Content is a bounded excerpt + the true length (full text fetched lazily elsewhere).
    assert body["content"]["length"] == 9000
    assert len(body["content"]["excerpt"]) == 4000


def test_detail_includes_processing_telemetry(tmp_path: Path) -> None:
    from doktok_contracts.ports import (
        AuditLogRepository,
        CategoryRepository,
        FeatureRepository,
        RecordRepository,
    )
    from doktok_contracts.schemas import (
        DocumentFeature,
        FeatureMetrics,
        FeatureStatus,
    )
    from doktok_core.aggregation.inmemory import InMemoryRecordRepository
    from doktok_core.audit.inmemory import InMemoryAuditLogRepository
    from doktok_core.categories.inmemory import InMemoryCategoryRepository
    from doktok_core.features.inmemory import InMemoryFeatureRepository

    (tmp_path / "content.md").write_text("body", encoding="utf-8")
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="scan.pdf",
        title="scan",
        status=DocumentStatus.ACTIVE,
        storage_path=str(tmp_path),
        created_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
        metadata={
            "extraction_method": "ocr",
            "page_count": 3,
            "ocr_confidence": 0.88,
            "language": "en",
            "normalized_from": "application/x-docx",
        },
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    feature_repo = InMemoryFeatureRepository()
    now = datetime.now(UTC)
    feature_repo.rows.append(
        DocumentFeature(
            id="f1",
            tenant_id="tenant-a",
            document_id="d1",
            feature="doc_metadata",
            status=FeatureStatus.DONE,
            attempts=1,
            last_attempt_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
            metrics=FeatureMetrics(
                duration_ms=1500, prompt_tokens=300, answer_tokens=80, model="qwen3.6:27b"
            ),
        )
    )

    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, InMemoryEntityRepository())  # type: ignore[type-abstract]
    registry.register(FeatureRepository, feature_repo)  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(RecordRepository, InMemoryRecordRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))

    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    proc = body["processing"]
    assert proc["extraction_method"] == "ocr"
    assert proc["ocr_outcome"] == "done"
    assert proc["page_count"] == 3
    assert proc["ocr_confidence"] == 0.88
    assert proc["normalized_from_mime"] == "application/x-docx"
    assert proc["language"] == "en"
    step = next(s for s in proc["steps"] if s["feature"] == "doc_metadata")
    assert step["label"] == "Metadata"  # from the feature catalog
    assert step["duration_ms"] == 1500
    assert step["total_tokens"] == 380
    assert step["model"] == "qwen3.6:27b"
    assert proc["total_duration_ms"] == 1500
    assert proc["total_tokens"] == 380


def _records_client(tmp_path: Path, records: list[ExtractedRecord] | None = None) -> TestClient:
    from doktok_contracts.ports import (
        AuditLogRepository,
        CategoryRepository,
        FeatureRepository,
        RecordRepository,
    )
    from doktok_core.aggregation.inmemory import InMemoryRecordRepository
    from doktok_core.audit.inmemory import InMemoryAuditLogRepository
    from doktok_core.categories.inmemory import InMemoryCategoryRepository
    from doktok_core.features.inmemory import InMemoryFeatureRepository

    (tmp_path / "content.md").write_text("body", encoding="utf-8")
    doc = Document(
        id="d1",
        tenant_id="tenant-a",
        sha256="a" * 64,
        original_filename="statement.pdf",
        title="statement",
        status=DocumentStatus.ACTIVE,
        storage_path=str(tmp_path),
        created_at=datetime.now(UTC),
    )
    doc_repo = InMemoryDocumentRepository()
    doc_repo.add(doc)
    rec_repo = InMemoryRecordRepository()
    if records:
        rec_repo.replace_for_document("tenant-a", "d1", records)
    registry = build_registry()
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(EntityRepository, InMemoryEntityRepository())  # type: ignore[type-abstract]
    registry.register(FeatureRepository, InMemoryFeatureRepository())  # type: ignore[type-abstract]
    registry.register(CategoryRepository, InMemoryCategoryRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(RecordRepository, rec_repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _txn(rid: str, **kw: object) -> ExtractedRecord:
    return ExtractedRecord(
        id=rid,
        tenant_id="tenant-a",
        document_id="d1",
        raw_text=rid,
        **kw,  # type: ignore[arg-type]
    )


def test_detail_folds_record_summary(tmp_path: Path) -> None:
    from datetime import date

    client = _records_client(
        tmp_path,
        [
            _txn(
                "r1",
                amount_minor=4250,
                currency="EUR",
                direction="debit",
                occurred_on=date(2024, 2, 3),
                merchant_normalized="block house",
            ),
            _txn(
                "r2",
                amount_minor=1000,
                currency="EUR",
                direction="credit",
                occurred_on=date(2024, 2, 9),
                merchant_normalized="block house",
            ),
        ],
    )
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    recs = body["records"]
    assert recs["total"] == 2
    eur = next(c for c in recs["by_currency"] if c["currency"] == "EUR")
    assert eur["debit_minor"] == 4250 and eur["credit_minor"] == 1000 and eur["count"] == 2
    assert recs["date_from"] == "2024-02-03" and recs["date_to"] == "2024-02-09"
    assert recs["top_merchants"][0]["merchant"] == "block house"
    # Honest confidence: nothing scores today, so both rows are unscored.
    assert recs["confidence"]["unscored"] == 2
    assert recs["low_confidence_count"] == 0


def test_detail_record_summary_empty_when_no_records(tmp_path: Path) -> None:
    body = _records_client(tmp_path).get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert body["records"]["total"] == 0
    assert body["records"]["by_currency"] == []


def test_records_endpoint_paginates(tmp_path: Path) -> None:
    from datetime import date

    rows = [
        _txn(f"r{i:02d}", occurred_on=date(2024, 1, i + 1), merchant_normalized=f"m{i}")
        for i in range(5)
    ]
    client = _records_client(tmp_path, rows)
    page1 = client.get("/api/v1/documents/d1/records?limit=2&offset=0", headers=_auth()).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert page1["next_offset"] == 2
    last = client.get("/api/v1/documents/d1/records?limit=2&offset=4", headers=_auth()).json()
    assert len(last["items"]) == 1
    assert last["next_offset"] is None  # last page


def test_records_endpoint_empty_document(tmp_path: Path) -> None:
    body = _records_client(tmp_path).get("/api/v1/documents/d1/records", headers=_auth()).json()
    assert body == {"items": [], "total": 0, "next_offset": None}


def test_records_endpoint_limit_bound_is_422(tmp_path: Path) -> None:
    client = _records_client(tmp_path)
    assert client.get("/api/v1/documents/d1/records?limit=0", headers=_auth()).status_code == 422
    assert client.get("/api/v1/documents/d1/records?limit=201", headers=_auth()).status_code == 422
    assert client.get("/api/v1/documents/d1/records?offset=-1", headers=_auth()).status_code == 422


def test_records_endpoint_foreign_or_missing_doc_is_404(tmp_path: Path) -> None:
    client = _records_client(tmp_path)
    assert client.get("/api/v1/documents/missing/records", headers=_auth()).status_code == 404


def test_records_endpoint_requires_token(tmp_path: Path) -> None:
    assert _records_client(tmp_path).get("/api/v1/documents/d1/records").status_code == 401


def test_detail_other_tenant_is_404(tmp_path: Path) -> None:
    client = _detail_client(tmp_path)
    assert client.get("/api/v1/documents/missing/detail", headers=_auth()).status_code == 404


def test_detail_logs_a_view(tmp_path: Path) -> None:
    client = _detail_client(tmp_path)
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    # The view is logged and shows up in the card's own recent-activity trail.
    viewed = [e for e in body["recent_activity"] if e["event_type"] == "document.viewed"]
    assert viewed and viewed[0]["actor_kind"] == "user"


def test_detail_view_is_deduped_within_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # React StrictMode fires the detail GET twice on mount; the deterministic bucketed id collapses
    # rapid repeat opens to a single view row. Freeze the clock so the two GETs always land in the
    # SAME dedup bucket - otherwise a real bucket-boundary crossing between them flakes CI (the id
    # is `int(now.timestamp()) // _VIEW_DEDUP_SECONDS`).
    import doktok_api.routers.documents as documents_mod

    frozen = datetime(2026, 1, 1, 12, 0, 3, tzinfo=UTC)  # mid-bucket, not a boundary

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return frozen

    monkeypatch.setattr(documents_mod, "datetime", _FrozenDatetime)

    client = _detail_client(tmp_path)
    client.get("/api/v1/documents/d1/detail", headers=_auth())
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    viewed = [e for e in body["recent_activity"] if e["event_type"] == "document.viewed"]
    assert len(viewed) == 1


# ---- #732: detail facts (category rank, chunk count, extraction) ----


def test_detail_categories_follow_rank_order(tmp_path: Path) -> None:
    """#732: the detail payload's categories come rank-ordered (primary first), not alphabetical."""
    cats = InMemoryCategoryRepository()
    zeta = cats.create("tenant-a", "Zeta", "zeta")
    alpha = cats.create("tenant-a", "Alpha", "alpha")
    assert zeta is not None and alpha is not None
    # Zeta assigned first (rank 0 = primary): alphabetical order would flip them.
    cats.set_document_categories("tenant-a", "d1", [zeta.id, alpha.id])
    client = _client(str(tmp_path), cats=cats)
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert [c["name"] for c in body["categories"]] == ["Zeta", "Alpha"]


def test_detail_reports_chunk_count_and_extraction(tmp_path: Path) -> None:
    """#732: chunk_count from the chunk store; extraction method + OCR confidence read from
    content.json."""
    (tmp_path / "content.json").write_text(
        json.dumps({"extraction_method": "ocr", "ocr_confidence": 0.91}), encoding="utf-8"
    )
    chunks = InMemoryChunkRepository()
    chunks.add_chunks(
        [
            DocumentChunk(
                id=f"c{i}", tenant_id="tenant-a", document_id="d1", version_id="v1", text=f"t{i}"
            )
            for i in range(3)
        ],
        [[0.1] * 4] * 3,
    )
    client = _client(str(tmp_path), chunks=chunks)
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert body["chunk_count"] == 3
    assert body["extraction"] == {"method": "ocr", "ocr_confidence": 0.91}


def test_detail_facts_default_without_chunks_or_content_json(tmp_path: Path) -> None:
    client = _client(str(tmp_path))
    body = client.get("/api/v1/documents/d1/detail", headers=_auth()).json()
    assert body["chunk_count"] == 0
    assert body["extraction"] == {"method": "", "ocr_confidence": None}


# ---- #730: similar documents endpoint ----


def _vec(a: float, b: float) -> list[float]:
    norm = (a**2 + b**2) ** 0.5
    return [a / norm, b / norm]


def test_similar_documents_ranked_enriched_and_self_excluded(tmp_path: Path) -> None:
    d2 = Document(
        id="d2",
        tenant_id="tenant-a",
        sha256="b" * 64,
        original_filename="twin.pdf",
        detected_mime="application/pdf",
        title="Twin document",
        status=DocumentStatus.ACTIVE,
        storage_path=str(tmp_path / "d2"),
        created_at=datetime.now(UTC),
    )
    chunks = InMemoryChunkRepository()
    chunks.add_chunks(
        [DocumentChunk(id="ca", tenant_id="tenant-a", document_id="d1", version_id="v1", text="a")],
        [_vec(1.0, 0.0)],
    )
    chunks.add_chunks(
        [DocumentChunk(id="cb", tenant_id="tenant-a", document_id="d2", version_id="v1", text="b")],
        [_vec(0.95, 0.05)],  # close to d1's chunk
    )
    client = _client(str(tmp_path), chunks=chunks, more_docs=[d2])
    resp = client.get("/api/v1/documents/d1/similar", headers=_auth())
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert [s["document_id"] for s in items] == ["d2"]  # self excluded
    assert items[0]["title"] == "Twin document"  # identity enriched from the document repo
    assert items[0]["original_filename"] == "twin.pdf"
    assert items[0]["score"] > 0.99


def test_similar_documents_empty_and_404(tmp_path: Path) -> None:
    chunks = InMemoryChunkRepository()
    chunks.add_chunks(
        [DocumentChunk(id="ca", tenant_id="tenant-a", document_id="d1", version_id="v1", text="a")],
        [_vec(1.0, 0.0)],
    )
    client = _client(str(tmp_path), chunks=chunks)
    assert client.get("/api/v1/documents/d1/similar", headers=_auth()).json() == []
    assert client.get("/api/v1/documents/missing/similar", headers=_auth()).status_code == 404
