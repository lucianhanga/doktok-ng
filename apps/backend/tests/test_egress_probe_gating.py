"""Egress gating of the AI test/probe endpoints (#622, security audit F-07).

test-openai opens TLS to api.openai.com; test-ollama/warmup-ollama probe a caller-supplied URL.
Under no-egress those connections violate the deployment's guarantee (and made these endpoints a
blind SSRF into the host's network). The gate: probes that would leave the host are refused with
422 while the effective no-egress posture is on; loopback probes keep working.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_api.routers import settings as settings_mod
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import ApiToken, Tenant, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer tok-admin"}  # tenant admin (probe endpoints are admin-gated)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probes must never touch the network in tests; the gating happens before them anyway."""
    monkeypatch.setattr(
        settings_mod, "_probe_ollama", lambda url: (True, "reachable - 1 model(s) installed", ["m"])
    )
    monkeypatch.setattr(settings_mod, "_warmup_ollama", lambda url, model: (True, "loaded"))
    monkeypatch.setattr(settings_mod, "_probe_openai", lambda key: (True, "valid"))


def _make_client(tmp_path: Path, *, no_egress: bool, locked: bool = False) -> TestClient:
    mem = InMemoryTenantRegistry()
    mem.create_tenant(Tenant(id="t", name="T"))
    mem.create_user(User(id="u", tenant_id="t", email="a@t.example", role="admin"))
    mem.create_api_token(
        ApiToken(id="tok-admin", tenant_id="t", user_id="u", token_sha256=hash_token("tok-admin"))
    )
    registry = build_registry()
    registry.register(TenantRegistry, mem)  # type: ignore[type-abstract]
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={},
        files_root=str(tmp_path),
        no_egress=no_egress,
        no_egress_lock=locked,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry))


def test_test_openai_is_refused_under_no_egress(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=True)
    resp = client.post("/api/v1/settings/ai/test-openai", headers=ADMIN, json={"api_key": "k"})
    assert resp.status_code == 422


def test_test_openai_is_refused_even_when_host_locked(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=True, locked=True)
    resp = client.post("/api/v1/settings/ai/test-openai", headers=ADMIN, json={"api_key": "k"})
    assert resp.status_code == 422


def test_test_ollama_remote_url_is_refused_under_no_egress(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=True)
    for url in ("http://169.254.169.254/", "http://192.168.1.10:11434", "http://10.0.0.1:11434"):
        resp = client.post("/api/v1/settings/ai/test-ollama", headers=ADMIN, json={"url": url})
        assert resp.status_code == 422, url


def test_warmup_ollama_remote_url_is_refused_under_no_egress(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=True)
    resp = client.post(
        "/api/v1/settings/ai/warmup-ollama",
        headers=ADMIN,
        json={"url": "http://192.168.1.10:11434", "model": "m"},
    )
    assert resp.status_code == 422


def test_loopback_probes_keep_working_under_no_egress(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=True)
    resp = client.post(
        "/api/v1/settings/ai/test-ollama", headers=ADMIN, json={"url": "http://localhost:11434"}
    )
    assert resp.status_code == 200
    resp = client.post(
        "/api/v1/settings/ai/warmup-ollama",
        headers=ADMIN,
        json={"url": "http://127.0.0.1:11434", "model": "m"},
    )
    assert resp.status_code == 200


def test_remote_urls_are_allowed_when_egress_is_permitted(tmp_path: Path) -> None:
    client = _make_client(tmp_path, no_egress=False)
    resp = client.post(
        "/api/v1/settings/ai/test-ollama",
        headers=ADMIN,
        json={"url": "http://192.168.1.10:11434"},
    )
    assert resp.status_code == 200
    resp = client.post("/api/v1/settings/ai/test-openai", headers=ADMIN, json={"api_key": "k"})
    assert resp.status_code == 200
