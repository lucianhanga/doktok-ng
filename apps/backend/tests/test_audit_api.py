import os
from datetime import UTC, datetime

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AuditLogRepository
from doktok_contracts.schemas import AuditEvent
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _event(tenant: str, event_type: str, document_id: str | None) -> AuditEvent:
    return AuditEvent(
        id=f"{tenant}-{event_type}-{document_id}",
        tenant_id=tenant,
        event_type=event_type,
        actor="worker",
        document_id=document_id,
        timestamp=datetime.now(UTC),
    )


def _client(*events: AuditEvent) -> TestClient:
    repo = InMemoryAuditLogRepository()
    for event in events:
        repo.record(event)
    registry = build_registry()
    registry.register(AuditLogRepository, repo)  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_requires_token() -> None:
    assert _client().get("/api/v1/audit").status_code == 401


def test_lists_only_callers_tenant_activity() -> None:
    client = _client(
        _event("tenant-a", "document.activated", "doc-a"),
        _event("tenant-b", "document.activated", "doc-b"),
    )
    response = client.get("/api/v1/audit", headers=_auth("tok-a"))
    assert response.status_code == 200
    rows = response.json()
    assert {r["document_id"] for r in rows} == {"doc-a"}


def test_filter_by_document() -> None:
    client = _client(
        _event("tenant-a", "document.received", "doc-a"),
        _event("tenant-a", "document.activated", "doc-a"),
        _event("tenant-a", "document.received", "doc-x"),
    )
    rows = client.get("/api/v1/audit?document_id=doc-a", headers=_auth("tok-a")).json()
    assert len(rows) == 2
    assert all(r["document_id"] == "doc-a" for r in rows)
