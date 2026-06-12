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


def _client(repo: FakeFeatureRepository) -> TestClient:
    registry = build_registry()
    registry.register(FeatureRepository, repo)  # type: ignore[type-abstract]
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
