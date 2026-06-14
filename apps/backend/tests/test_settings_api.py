import os

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository
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


def _client() -> TestClient:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, registry=registry))


AUTH = {"Authorization": "Bearer tok-a"}


def test_requires_token() -> None:
    assert _client().get("/api/v1/settings/ai").status_code == 401


def test_catalog_lists_models_per_purpose() -> None:
    body = _client().get("/api/v1/settings/ai/catalog", headers=AUTH).json()
    assert {m["model"] for m in body["pipeline"]} >= {"qwen3:14b", "qwen3.6:35b-a3b"}
    assert body["reasoning_levels"] == ["off", "low", "medium", "high"]


def test_get_defaults_and_update_roundtrip() -> None:
    client = _client()
    defaults = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert defaults["pipeline"]["model"] == "qwen3:14b"
    assert defaults["rag"]["model"] == "qwen3.6:35b-a3b"
    assert defaults["openai_api_key_set"] is False

    resp = client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3.6:35b-a3b",
                "num_ctx": 16384,
                "reasoning": "high",
            },
            "rag": {
                "provider": "ollama",
                "model": "qwen3:14b",
                "num_ctx": 32768,
                "reasoning": "low",
            },
            "openai_api_key": "dummy-test-value",  # pragma: allowlist secret
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    saved = resp.json()
    assert (
        saved["pipeline"]["model"] == "qwen3.6:35b-a3b" and saved["pipeline"]["reasoning"] == "high"
    )
    assert saved["rag"]["model"] == "qwen3:14b"
    # The key is stored but never returned - only that it is set.
    assert saved["openai_api_key_set"] is True
    assert "openai_api_key" not in saved

    again = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert again["pipeline"]["num_ctx"] == 16384 and again["openai_api_key_set"] is True


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
    registry.register(ChatModelProvider, object())
    registry.register(RagAnswerer, object())
    settings = Settings(env="test", tenant_tokens=TOKENS, _env_file=None)  # type: ignore[call-arg]
    client = TestClient(create_app(settings=settings, registry=registry))

    client.put(
        "/api/v1/settings/ai",
        json={
            "pipeline": {
                "provider": "ollama",
                "model": "qwen3:14b",
                "num_ctx": 16384,
                "reasoning": "off",
            },
            "rag": {
                "provider": "ollama",
                "model": "qwen3:14b",
                "num_ctx": 32768,
                "reasoning": "low",
            },
        },
        headers=AUTH,
    )
    assert registry.is_registered(ChatModelProvider) is False
    assert registry.is_registered(RagAnswerer) is False
