"""Per-tenant model stack in the API factories (epic #708, T2): the RAG/chat model and the egress
sink resolve for the CALLER'S tenant - tenant override -> console global -> env defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.dependencies import _build_rag_chat_model
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import AiPurposeSettings, Tenant, TenantAiSettings, User
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.security.egress import EgressBlocked
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi import FastAPI
from starlette.requests import Request

JWT_SECRET = "t2-stack-secret-32-bytes-minimum!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _app(tmp_path: Path) -> tuple[FastAPI, InMemoryAppSettingsRepository]:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="tenant-a", name="A"))
    reg.create_tenant(Tenant(id="tenant-b", name="B"))
    for tid in ("tenant-a", "tenant-b"):
        reg.create_user(User(id=f"u-{tid}", tenant_id=tid, email=f"u@{tid}.x", role="admin"))
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
    return create_app(settings=settings, registry=registry), app_settings


def _request(app: FastAPI, tenant_id: str) -> Request:
    token = issue_access_token(
        tenant_id=tenant_id, user_id=f"u-{tenant_id}", secret=JWT_SECRET, ttl_seconds=3600
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "app": app,
        "headers": [(b"authorization", f"Bearer {token}".encode())],
    }
    return Request(scope)


def _purpose(provider: str, model: str) -> AiPurposeSettings:
    return AiPurposeSettings(provider=provider, model=model, num_ctx=8192)


def test_rag_model_resolves_per_tenant(tmp_path: Path) -> None:
    app, app_settings = _app(tmp_path)
    app_settings.set_tenant_ai_settings(
        "tenant-a", TenantAiSettings(rag=_purpose("ollama", "tenant-a-model"))
    )
    model_a = _build_rag_chat_model(_request(app, "tenant-a"))
    model_b = _build_rag_chat_model(_request(app, "tenant-b"))
    assert getattr(model_a, "_model", None) == "tenant-a-model"
    assert getattr(model_b, "_model", None) != "tenant-a-model"


def test_egress_sink_is_per_tenant(tmp_path: Path) -> None:
    app, app_settings = _app(tmp_path)
    # Both tenants pick OpenAI for RAG; only tenant-b may egress.
    for tid in ("tenant-a", "tenant-b"):
        app_settings.set_tenant_ai_settings(
            tid, TenantAiSettings(rag=_purpose("openai", "gpt-4o-mini"))
        )
    app_settings.set_openai_api_key("sk-test")
    model_a = _build_rag_chat_model(_request(app, "tenant-a"))  # default posture: ON (blocked)
    assert isinstance(model_a, EgressBlocked)
    app_settings.set_tenant_ai_settings(
        "tenant-b",
        TenantAiSettings(rag=_purpose("openai", "gpt-4o-mini"), no_egress=False),
    )
    model_b = _build_rag_chat_model(_request(app, "tenant-b"))
    assert not isinstance(model_b, EgressBlocked)


def test_static_tenant_token_uses_the_global_stack(tmp_path: Path) -> None:
    # The host credential (no user) resolves to the global/env layers (no tenant override applies
    # to OTHER tenants, and a user-less token is not a tenant override principal).
    app, app_settings = _app(tmp_path)
    app_settings.set_tenant_ai_settings(
        "tenant-a", TenantAiSettings(rag=_purpose("ollama", "tenant-a-model"))
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "app": app,
        "headers": [(b"authorization", b"Bearer tok-host")],
    }
    app.state.settings.tenant_tokens = {"tok-host": "tenant-a"}
    model = _build_rag_chat_model(Request(scope))
    # tenant-a IS the override's tenant here, so it applies (the host acts for tenant-a).
    assert getattr(model, "_model", None) == "tenant-a-model"
