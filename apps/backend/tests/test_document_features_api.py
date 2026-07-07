import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository, DocumentRepository, FeatureRepository
from doktok_contracts.schemas import Document, DocumentFeature, DocumentStatus, FeatureStatus
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.documents.inmemory import InMemoryDocumentRepository
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class FakeFeatureRepository:
    def __init__(self) -> None:
        self.reset_calls: list[tuple[str, str, str]] = []

    def record_done(self, tenant_id, document_id, feature, feature_version) -> None: ...  # type: ignore[no-untyped-def]
    def ensure_for_active(self, tenant_id, features) -> int:  # type: ignore[no-untyped-def]
        return 0

    def seed_for_document(self, tenant_id, document_id, stages) -> int:  # type: ignore[no-untyped-def]
        return 0

    def claim_next(self, tenant_id, *, now, reclaim_before, dependencies=()):  # type: ignore[no-untyped-def]
        return None

    def mark_done(self, feature_id, *, feature_version) -> None: ...  # type: ignore[no-untyped-def]
    def mark_failed(self, feature_id, *, error, next_attempt_at) -> None: ...  # type: ignore[no-untyped-def]

    def list_for_document(self, tenant_id: str, document_id: str) -> list[DocumentFeature]:
        now = datetime.now(UTC)
        return [
            DocumentFeature(
                id="f1",
                tenant_id=tenant_id,
                document_id=document_id,
                feature="entities",
                status=FeatureStatus.FAILED,
                attempts=3,
                last_error="boom",
                created_at=now,
                updated_at=now,
            )
        ]

    def list_for_tenant(self, tenant_id: str, *, limit: int = 2000) -> list[DocumentFeature]:
        return self.list_for_document(tenant_id, "d1")

    def reset(self, tenant_id: str, document_id: str, feature: str) -> bool:
        self.reset_calls.append((tenant_id, document_id, feature))
        return feature == "entities"

    def requeue_running(self, tenant_id: str) -> int:
        return 0


def _doc(doc_id: str, tenant: str) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant,
        sha256=(doc_id + "a" * 64)[:64],
        original_filename=f"{doc_id}.txt",
        detected_mime="text/plain",
        title=doc_id,
        status=DocumentStatus.ACTIVE,
        storage_path=f"/docs/{doc_id}",
        created_at=datetime.now(UTC),
        activated_at=datetime.now(UTC),
    )


def _client(repo: FakeFeatureRepository) -> TestClient:
    registry = build_registry()
    registry.register(FeatureRepository, repo)
    registry.register(
        AuditLogRepository,  # type: ignore[type-abstract]
        InMemoryAuditLogRepository(),
    )
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _client_with_docs(feat_repo: FakeFeatureRepository, *docs: Document) -> TestClient:
    doc_repo = InMemoryDocumentRepository()
    for doc in docs:
        doc_repo.add(doc)
    registry = build_registry()
    registry.register(FeatureRepository, feat_repo)
    registry.register(DocumentRepository, doc_repo)  # type: ignore[type-abstract]
    registry.register(
        AuditLogRepository,  # type: ignore[type-abstract]
        InMemoryAuditLogRepository(),
    )
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-a"}


def test_features_requires_token() -> None:
    assert _client(FakeFeatureRepository()).get("/api/v1/documents/d1/features").status_code == 401


def test_lists_document_features() -> None:
    body = (
        _client(FakeFeatureRepository())
        .get("/api/v1/documents/d1/features", headers=_auth())
        .json()
    )
    assert body[0]["feature"] == "entities"
    assert body[0]["status"] == "failed"
    assert body[0]["last_error"] == "boom"


def test_retry_resets_the_feature() -> None:
    repo = FakeFeatureRepository()
    resp = _client(repo).post("/api/v1/documents/d1/features/entities/retry", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == {"status": "queued"}
    assert repo.reset_calls == [("tenant-a", "d1", "entities")]


def test_retry_unknown_feature_is_404() -> None:
    resp = _client(FakeFeatureRepository()).post(
        "/api/v1/documents/d1/features/missing/retry", headers=_auth()
    )
    assert resp.status_code == 404


def test_reprocess_all_resets_feature_for_every_tenant_doc() -> None:
    repo = FakeFeatureRepository()
    d1 = _doc("doc-1", "tenant-a")
    d2 = _doc("doc-2", "tenant-a")
    resp = _client_with_docs(repo, d1, d2).post(
        "/api/v1/documents/features/entities/reprocess-all", headers=_auth()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["count"] == 2
    assert sorted(repo.reset_calls) == [
        ("tenant-a", "doc-1", "entities"),
        ("tenant-a", "doc-2", "entities"),
    ]


def test_reprocess_all_unknown_feature_is_404() -> None:
    resp = _client_with_docs(FakeFeatureRepository()).post(
        "/api/v1/documents/features/no-such-feature/reprocess-all", headers=_auth()
    )
    assert resp.status_code == 404


def test_reprocess_all_counts_only_successes() -> None:
    """reset() returns False for any feature other than 'entities'; count must be 0."""
    repo = FakeFeatureRepository()
    d1 = _doc("doc-1", "tenant-a")
    # "thumbnail" is a valid catalog feature; FakeFeatureRepository.reset returns False for it
    resp = _client_with_docs(repo, d1).post(
        "/api/v1/documents/features/thumbnail/reprocess-all", headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    # reset was still called — the feature just had no existing row to reset
    assert repo.reset_calls == [("tenant-a", "doc-1", "thumbnail")]


def test_reprocess_all_tenant_isolation() -> None:
    """Documents owned by a different tenant are never touched."""
    repo = FakeFeatureRepository()
    own = _doc("own-doc", "tenant-a")
    other = _doc("other-doc", "tenant-b")
    resp = _client_with_docs(repo, own, other).post(
        "/api/v1/documents/features/entities/reprocess-all", headers=_auth()
    )
    assert resp.status_code == 200
    touched_tenants = {call[0] for call in repo.reset_calls}
    touched_docs = {call[1] for call in repo.reset_calls}
    assert touched_tenants == {"tenant-a"}
    assert "other-doc" not in touched_docs
