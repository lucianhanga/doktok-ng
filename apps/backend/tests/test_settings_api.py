import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository, AuditLogRepository
from doktok_core.audit.inmemory import InMemoryAuditLogRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client_with_audit(
    *, no_egress: bool = True, no_egress_lock: bool = False
) -> tuple[TestClient, InMemoryAuditLogRepository]:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    audit = InMemoryAuditLogRepository()
    registry.register(AuditLogRepository, audit)  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens=TOKENS,
        no_egress=no_egress,
        no_egress_lock=no_egress_lock,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings, registry=registry)), audit


def _client(*, no_egress: bool = True, no_egress_lock: bool = False) -> TestClient:
    return _client_with_audit(no_egress=no_egress, no_egress_lock=no_egress_lock)[0]


AUTH = {"Authorization": "Bearer tok-a"}


def test_requires_token() -> None:
    assert _client().get("/api/v1/settings/ai").status_code == 401


def test_catalog_lists_models_per_purpose() -> None:
    body = _client().get("/api/v1/settings/ai/catalog", headers=AUTH).json()
    assert {m["model"] for m in body["pipeline"]} >= {"qwen3.6:27b"}
    assert body["reasoning_levels"] == ["off", "low", "medium", "high"]


def test_get_defaults_and_update_roundtrip() -> None:
    client = _client()
    defaults = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert defaults["pipeline"]["model"] == "qwen3.6:27b"
    assert defaults["rag"]["model"] == "qwen3.6:27b"
    assert defaults["openai_api_key_set"] is False

    assert defaults["ner"]["provider"] == "openai"  # ADR-0023 default
    assert defaults["keg"]["provider"] == "gliner-relex"  # ADR-0023 default

    resp = client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 16384,
                "reasoning": "high",
            },
            "ner": dict(_LOCAL_NER),
            "keg": dict(_LOCAL_KEG),
            "rag": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 32768,
                "reasoning": "low",
            },
            "openai_api_key": "dummy-test-value",  # pragma: allowlist secret
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["ner"]["provider"] == "gliner" and saved["keg"]["provider"] == "gliner-relex"
    assert saved["pipeline"]["model"] == "qwen3.6:27b" and saved["pipeline"]["reasoning"] == "high"
    assert saved["rag"]["model"] == "qwen3.6:27b"
    # The key is stored but never returned - only that it is set.
    assert saved["openai_api_key_set"] is True
    assert "openai_api_key" not in saved

    again = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert again["pipeline"]["num_ctx"] == 16384 and again["openai_api_key_set"] is True


def test_per_purpose_ollama_url_default_and_override_roundtrip() -> None:
    # M13 #369: GET exposes the effective default URL; per-purpose + embedding overrides round-trip.
    # A remote (non-loopback) Ollama URL is egress, so this needs no-egress off.
    client = _client(no_egress=False)
    defaults = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert defaults["ollama_base_url_default"] == "http://localhost:11434"
    assert defaults["pipeline"]["ollama_base_url"] is None
    assert defaults["embedding"]["ollama_base_url"] is None

    resp = client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 8192,
                "reasoning": "off",
                "ollama_base_url": "http://gpu-box:11434",
            },
            "rag": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 32768,
                "reasoning": "off",
            },
            "embedding": {"ollama_base_url": "http://embed-host:11434"},
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    again = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert again["pipeline"]["ollama_base_url"] == "http://gpu-box:11434"
    assert again["embedding"]["ollama_base_url"] == "http://embed-host:11434"
    assert again["rag"]["ollama_base_url"] is None  # left unset -> inherits the default


def test_invalid_ollama_url_is_rejected() -> None:
    client = _client()
    resp = client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 8192,
                "reasoning": "off",
                "ollama_base_url": "not-a-url",
            },
            "rag": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 32768,
                "reasoning": "off",
            },
        },
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_test_ollama_rejects_bad_url() -> None:
    client = _client()
    resp = client.post("/api/v1/settings/ai/test-ollama", json={"url": "not-a-url"}, headers=AUTH)
    assert resp.status_code == 422


def test_test_ollama_reports_unreachable() -> None:
    # A closed port returns ok:false with a detail (never raises), and echoes the probed URL.
    client = _client()
    resp = client.post(
        "/api/v1/settings/ai/test-ollama",
        json={"url": "http://127.0.0.1:1"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and body["url"] == "http://127.0.0.1:1" and body["detail"]


def test_test_ollama_reports_selected_model_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # When a model is supplied, Test also reports whether it is installed (no model load involved).
    from doktok_api.routers import settings as settings_router

    monkeypatch.setattr(
        settings_router,
        "_probe_ollama",
        lambda url: (True, "reachable - 2 model(s) installed", ["qwen3:30b", "nomic-embed:latest"]),
    )
    client = _client(no_egress=False)  # remote probe URLs need egress permitted (#622)
    resp = client.post(
        "/api/v1/settings/ai/test-ollama",
        json={"url": "http://10.0.0.5:11434", "model": "qwen3:30b"},
        headers=AUTH,
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "qwen3:30b"
    assert body["model_present"] is True
    assert "installed" in body["detail"]


def test_test_ollama_flags_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from doktok_api.routers import settings as settings_router

    monkeypatch.setattr(
        settings_router,
        "_probe_ollama",
        lambda url: (True, "reachable - 1 model(s) installed", ["nomic-embed:latest"]),
    )
    client = _client(no_egress=False)  # remote probe URLs need egress permitted (#622)
    resp = client.post(
        "/api/v1/settings/ai/test-ollama",
        json={"url": "http://10.0.0.5:11434", "model": "qwen3:30b"},
        headers=AUTH,
    )
    body = resp.json()
    assert body["ok"] is True  # the server is reachable...
    assert body["model_present"] is False  # ...but the selected model is not installed
    assert "NOT installed" in body["detail"] and "ollama pull qwen3:30b" in body["detail"]


def test_warmup_ollama_loads_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from doktok_api.routers import settings as settings_router

    monkeypatch.setattr(
        settings_router, "_warmup_ollama", lambda url, model: (True, f"model '{model}' loaded")
    )
    client = _client(no_egress=False)  # remote probe URLs need egress permitted (#622)
    resp = client.post(
        "/api/v1/settings/ai/warmup-ollama",
        json={"url": "http://10.0.0.5:11434", "model": "qwen3:30b"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["model"] == "qwen3:30b" and "loaded" in body["detail"]


def test_warmup_ollama_rejects_bad_url() -> None:
    client = _client()
    resp = client.post(
        "/api/v1/settings/ai/warmup-ollama",
        json={"url": "not-a-url", "model": "qwen3:30b"},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_saving_settings_records_a_non_secret_activity_event() -> None:
    # M15 #373: a settings change is recorded in the activity log, without the OpenAI key.
    client, audit = _client_with_audit()
    resp = client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 8192,
                "reasoning": "off",
            },
            "ner": dict(_LOCAL_NER),
            "keg": dict(_LOCAL_KEG),
            "rag": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 32768,
                "reasoning": "off",
            },
            "openai_api_key": "sk-super-secret",  # pragma: allowlist secret
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    events = audit.list_events("tenant-a")
    changed = [e for e in events if e.event_type == "settings.changed"]
    assert len(changed) == 1
    assert changed[0].document_id is None and changed[0].actor_kind == "user"
    # The key must never appear in the activity row (description or metadata).
    assert "sk-super-secret" not in (changed[0].description + str(changed[0].metadata))
    # OCR settings changes are recorded too.
    client.put("/api/v1/settings/ocr", json={"ocr_concurrency": 3}, headers=AUTH)
    ocr_events = [e for e in audit.list_events("tenant-a") if "OCR" in e.description]
    assert ocr_events and "3" in ocr_events[0].description


def test_ocr_recommendation_returns_a_valid_suggestion() -> None:
    # M17 #375: probes this host and returns a usable engine + concurrency (auth-gated).
    client = _client()
    assert client.get("/api/v1/settings/ocr/recommendation").status_code == 401
    rec = client.get("/api/v1/settings/ocr/recommendation", headers=AUTH).json()
    assert rec["engine"] in {"paddleocr", "rapidocr", "glm-ocr"}
    assert rec["concurrency"] >= 1 and rec["reason"]


def test_ollama_status_reflects_offloading() -> None:
    # M16 #374: default embedding keeps local Ollama needed; offloading all of it flips the flag.
    # Offloading to OpenAI + a remote embedding URL is egress, so this needs no-egress off.
    client = _client(no_egress=False)
    s = client.get("/api/v1/settings/ollama-status", headers=AUTH).json()
    assert s["local_ollama_needed"] is True  # default embedding uses the in-stack Ollama
    # Offload everything: OpenAI pipeline/RAG + a remote embedding URL.
    client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "num_ctx": 8192,
                "reasoning": "off",
            },
            "rag": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "num_ctx": 8192,
                "reasoning": "off",
            },
            "embedding": {"ollama_base_url": "http://10.0.0.22:11434"},
        },
        headers=AUTH,
    )
    s2 = client.get("/api/v1/settings/ollama-status", headers=AUTH).json()
    assert s2["local_ollama_needed"] is False
    assert s2["embedding_url"] == "http://10.0.0.22:11434"


def test_test_openai_reports_when_no_key_is_available() -> None:
    # M13 #372: with no candidate key and none stored, the probe reports failure (no network call).
    client = _client()
    resp = client.post("/api/v1/settings/ai/test-openai", json={"api_key": ""}, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and "no API key" in body["detail"]


def test_test_openai_requires_a_token() -> None:
    resp = _client().post("/api/v1/settings/ai/test-openai", json={"api_key": "sk-x"})
    assert resp.status_code == 401


def test_ocr_settings_default_and_update() -> None:
    client = _client()
    assert client.get("/api/v1/settings/ocr", headers=AUTH).json()["ocr_concurrency"] == 4
    resp = client.put("/api/v1/settings/ocr", json={"ocr_concurrency": 6}, headers=AUTH)
    assert resp.status_code == 200 and resp.json()["ocr_concurrency"] == 6
    assert client.get("/api/v1/settings/ocr", headers=AUTH).json()["ocr_concurrency"] == 6


def test_ocr_concurrency_is_bounded() -> None:
    client = _client()
    assert (
        client.put("/api/v1/settings/ocr", json={"ocr_concurrency": 0}, headers=AUTH).status_code
        == 422
    )
    assert (
        client.put("/api/v1/settings/ocr", json={"ocr_concurrency": 99}, headers=AUTH).status_code
        == 422
    )


def test_saving_ai_settings_clears_cached_providers() -> None:
    # Apply-on-save: PUT /ai drops the cached chat model + answerer so the next chat request
    # rebuilds them with the new selection (no backend restart).
    from doktok_contracts.ports import ChatModelProvider, RagAnswerer

    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    registry.register(AuditLogRepository, InMemoryAuditLogRepository())  # type: ignore[type-abstract]
    registry.register(ChatModelProvider, object())
    registry.register(RagAnswerer, object())
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))

    client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 16384,
                "reasoning": "off",
            },
            "ner": dict(_LOCAL_NER),
            "keg": dict(_LOCAL_KEG),
            "rag": {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 32768,
                "reasoning": "low",
            },
        },
        headers=AUTH,
    )
    assert registry.is_registered(ChatModelProvider) is False
    assert registry.is_registered(RagAnswerer) is False


_LOCAL_NER = {
    "provider": "gliner",
    "model": "gliner-community/gliner_large-v2.5",
    "num_ctx": 8192,
    "reasoning": "off",
}
_LOCAL_KEG = {
    "provider": "gliner-relex",
    "model": "knowledgator/gliner-relex-large-v1.0",
    "num_ctx": 8192,
    "reasoning": "off",
}


def _ai_body(pipeline: dict[str, object], **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "pipeline": pipeline,
        # NER/KEG default to no-egress-safe local span models so egress tests isolate the
        # pipeline/rag/url vector under test (callers override via **extra when needed).
        "ner": dict(_LOCAL_NER),
        "keg": dict(_LOCAL_KEG),
        "rag": {
            "provider": "ollama",
            "model": "qwen3.6:27b",
            "num_ctx": 32768,
            "reasoning": "off",
        },
    }
    body.update(extra)
    return body


def test_put_rejects_openai_pipeline_under_no_egress() -> None:
    client = _client(no_egress=True)
    resp = client.put(
        "/api/v1/settings/ai",
        json=_ai_body(
            {"provider": "openai", "model": "gpt-4o-mini", "num_ctx": 8192, "reasoning": "off"}
        ),
        headers=AUTH,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "egress_not_permitted"
    assert detail["violations"] == [
        {"purpose": "pipeline", "reason": "openai_selected", "value": "openai"}
    ]


def test_put_rejects_remote_ollama_url_under_no_egress() -> None:
    client = _client(no_egress=True)
    resp = client.put(
        "/api/v1/settings/ai",
        json=_ai_body(
            {
                "provider": "ollama",
                "model": "qwen3.6:27b",
                "num_ctx": 8192,
                "reasoning": "off",
                "ollama_base_url": "http://10.0.0.28:11434",
            }
        ),
        headers=AUTH,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["violations"] == [
        {"purpose": "pipeline", "reason": "remote_ollama_url", "value": "http://10.0.0.28:11434"}
    ]


def test_get_exposes_no_egress_and_purpose_status() -> None:
    body = _client(no_egress=True).get("/api/v1/settings/ai", headers=AUTH).json()
    assert body["no_egress"] is True
    # Default pipeline/RAG are local (loopback) Ollama -> usable, no egress.
    assert body["purpose_status"]["pipeline"] == {
        "requires_egress": False,
        "usable": True,
        "blocked_reason": None,
    }
    assert body["egress_active"] is False


def test_catalog_marks_openai_options_and_no_egress() -> None:
    body = _client(no_egress=True).get("/api/v1/settings/ai/catalog", headers=AUTH).json()
    assert body["no_egress"] is True
    by_model = {o["model"]: o["requires_egress"] for o in body["pipeline"]}
    assert by_model["qwen3.6:27b"] is False
    assert by_model["gpt-4o-mini"] is True


def test_enabling_openai_egress_is_audited() -> None:
    client, audit = _client_with_audit(no_egress=False)
    resp = client.put(
        "/api/v1/settings/ai",
        json=_ai_body(
            {"provider": "openai", "model": "gpt-4o-mini", "num_ctx": 8192, "reasoning": "off"},
            openai_api_key="sk-test",  # pragma: allowlist secret
        ),
        headers=AUTH,
    )
    assert resp.status_code == 200 and resp.json()["egress_active"] is True
    assert "egress.enabled" in [e.event_type for e in audit.list_events("tenant-a")]


def test_no_egress_toggle_persists_and_unblocks_openai_in_same_save() -> None:
    client = _client(no_egress=True)
    # Turn no-egress OFF and pick OpenAI in one save: validated against the NEW posture, so allowed.
    resp = client.put(
        "/api/v1/settings/ai",
        json=_ai_body(
            {"provider": "openai", "model": "gpt-4o-mini", "num_ctx": 8192, "reasoning": "off"},
            no_egress=False,
            openai_api_key="sk-test",  # pragma: allowlist secret
        ),
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["no_egress"] is False and body["no_egress_locked"] is False
    # Persisted (the in-app toggle, not the env default).
    assert client.get("/api/v1/settings/ai", headers=AUTH).json()["no_egress"] is False


def test_host_lock_forces_no_egress_and_rejects_disable() -> None:
    client = _client(no_egress=True, no_egress_lock=True)
    body = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert body["no_egress"] is True and body["no_egress_locked"] is True
    # Turning it off from the UI is refused while the host holds the lock.
    resp = client.put(
        "/api/v1/settings/ai",
        json=_ai_body(
            {"provider": "ollama", "model": "qwen3.6:27b", "num_ctx": 8192, "reasoning": "off"},
            no_egress=False,
        ),
        headers=AUTH,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "no_egress_locked"
