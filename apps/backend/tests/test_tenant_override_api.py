"""Tenant model-stack override API (epic #708, T3): tenant admins write their own partial
override; GET /settings/ai returns the tenant-effective stack + env defaults + the override."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

JWT_SECRET = "t3-override-secret-32-bytes-min!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client(tmp_path: Path) -> tuple[TestClient, InMemoryAppSettingsRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_user(User(id="u_admin", tenant_id="tenant-a", email="a@x.com", role="admin"))
    reg.create_user(User(id="u_view", tenant_id="tenant-a", email="v@x.com", role="viewer"))
    app_settings = InMemoryAppSettingsRepository()
    registry = build_registry()
    registry.register(TenantRegistry, reg)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, app_settings)  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        auth_jwt_secret=JWT_SECRET,
        files_root=str(tmp_path),
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), app_settings


def _bearer(user_id: str) -> dict[str, str]:
    token = issue_access_token(
        tenant_id="tenant-a", user_id=user_id, secret=JWT_SECRET, ttl_seconds=3600
    )
    return {"Authorization": f"Bearer {token}"}


ADMIN = _bearer("u_admin")
VIEWER = _bearer("u_view")
PURPOSE = {"provider": "ollama", "model": "tenant-model", "num_ctx": 8192, "reasoning": "off"}
OPENAI_PURPOSE = {"provider": "openai", "model": "gpt-4o-mini", "num_ctx": 8192, "reasoning": "off"}


def test_get_returns_effective_defaults_and_override(tmp_path: Path) -> None:
    client, app_settings = _client(tmp_path)
    # The schema-default NER is openai (ADR-0023): the override must bring it on-host too, or the
    # boundary refuses the save (as it does for any stack egressing under no-egress).
    resp = client.put(
        "/api/v1/settings/ai/override",
        json={"pipeline": PURPOSE, "ner": PURPOSE},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pipeline"]["model"] == "tenant-model"  # effective
    assert body["override"]["pipeline"]["model"] == "tenant-model"  # the tenant's own layer
    assert body["override"]["rag"] is None  # partial: unset purposes stay unset
    assert body["defaults"]["pipeline"]["provider"] == "ollama"  # env defaults block
    assert body["no_egress"] is True  # default posture


def test_put_requires_admin_role(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert (
        client.put(
            "/api/v1/settings/ai/override", json={"pipeline": PURPOSE}, headers=VIEWER
        ).status_code
        == 403
    )
    assert client.delete("/api/v1/settings/ai/override", headers=VIEWER).status_code == 403


def test_egress_selection_refused_while_no_egress_on(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    resp = client.put("/api/v1/settings/ai/override", json={"rag": OPENAI_PURPOSE}, headers=ADMIN)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "egress_not_permitted"


def test_egress_selection_allowed_when_turning_off_in_the_same_request(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    resp = client.put(
        "/api/v1/settings/ai/override",
        json={"rag": OPENAI_PURPOSE, "no_egress": False},
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["no_egress"] is False


def test_delete_resets_to_the_default_layers(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    client.put(
        "/api/v1/settings/ai/override",
        json={"pipeline": PURPOSE, "no_egress": False},
        headers=ADMIN,
    )
    resp = client.delete("/api/v1/settings/ai/override", headers=ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["override"] is None
    assert body["pipeline"]["model"] != "tenant-model"
    assert body["no_egress"] is True  # back to the default posture
