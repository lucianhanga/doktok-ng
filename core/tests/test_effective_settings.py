"""Per-tenant model-stack resolution (epic #708, T1): per purpose, tenant override -> global
saved settings -> env defaults; no_egress is per-tenant with the host lock on top. Embedding +
OCR stay deployment-global."""

from __future__ import annotations

from typing import Any

from doktok_contracts.schemas import AiPurposeSettings, AiSettings, TenantAiSettings
from doktok_core.config import Settings
from doktok_core.settings.effective import (
    effective_ai_settings,
    effective_openai_api_key,
    effective_tenant_no_egress,
)
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository


def _env(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def _purpose(provider: str, model: str) -> AiPurposeSettings:
    return AiPurposeSettings(provider=provider, model=model, num_ctx=8192)


def test_nothing_saved_resolves_to_env_defaults() -> None:
    repo = InMemoryAppSettingsRepository()
    env = _env(pipeline_provider="openai", rag_provider="openai")
    eff = effective_ai_settings(repo, "t1", env)
    assert eff.pipeline.provider == "openai"
    assert eff.rag.provider == "openai"
    # Untouched purposes keep the schema/env defaults.
    assert eff.ner == AiSettings().ner


def test_global_saved_beats_env() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_ai_settings(
        AiSettings(
            pipeline=_purpose("ollama", "global-model"),
            rag=_purpose("ollama", "global-rag"),
            ner=AiSettings().ner,
            keg=AiSettings().keg,
            rerank=AiSettings().rerank,
        )
    )
    eff = effective_ai_settings(repo, "t1", _env())
    assert eff.pipeline.model == "global-model"
    assert eff.rag.model == "global-rag"


def test_tenant_override_wins_per_purpose_and_falls_through() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_ai_settings(
        AiSettings(
            pipeline=_purpose("ollama", "global-model"),
            rag=_purpose("ollama", "global-rag"),
            ner=AiSettings().ner,
            keg=AiSettings().keg,
            rerank=AiSettings().rerank,
        )
    )
    repo.set_tenant_ai_settings("t1", TenantAiSettings(pipeline=_purpose("openai", "tenant-model")))
    eff = effective_ai_settings(repo, "t1", _env())
    assert eff.pipeline.model == "tenant-model"  # the override wins for pipeline
    assert eff.rag.model == "global-rag"  # unset purposes fall through to global
    assert eff.ner == AiSettings().ner


def test_tenant_override_is_tenant_scoped() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_tenant_ai_settings("t1", TenantAiSettings(pipeline=_purpose("openai", "t1-model")))
    assert effective_ai_settings(repo, "t2", _env()).pipeline.model != "t1-model"


def test_embedding_never_comes_from_the_tenant() -> None:
    repo = InMemoryAppSettingsRepository()
    override = TenantAiSettings(pipeline=_purpose("openai", "tenant-model"))
    repo.set_tenant_ai_settings("t1", override)
    eff = effective_ai_settings(repo, "t1", _env())
    assert eff.embedding == AiSettings().embedding  # the global/schema embedding path


# --- per-tenant no_egress ---


def test_no_egress_defaults_on() -> None:
    repo = InMemoryAppSettingsRepository()
    assert effective_tenant_no_egress(repo, "t1", _env()) is True


def test_no_egress_env_default_applies() -> None:
    repo = InMemoryAppSettingsRepository()
    assert effective_tenant_no_egress(repo, "t1", _env(no_egress=False)) is False


def test_no_egress_global_saved_beats_env() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_no_egress(True)
    assert effective_tenant_no_egress(repo, "t1", _env(no_egress=False)) is True


def test_no_egress_tenant_beats_global() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_no_egress(True)
    repo.set_tenant_ai_settings("t1", TenantAiSettings(no_egress=False))
    assert effective_tenant_no_egress(repo, "t1", _env()) is False
    assert effective_tenant_no_egress(repo, "t2", _env()) is True  # other tenants unaffected


def test_no_egress_lock_forces_on() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_tenant_ai_settings("t1", TenantAiSettings(no_egress=False))
    assert effective_tenant_no_egress(repo, "t1", _env(no_egress_lock=True)) is True


# --- the tenant store itself ---


def test_tenant_store_round_trip_and_delete() -> None:
    repo = InMemoryAppSettingsRepository()
    assert repo.get_tenant_ai_settings("t1") is None
    override = TenantAiSettings(rag=_purpose("ollama", "rag-model"), no_egress=False)
    repo.set_tenant_ai_settings("t1", override)
    loaded = repo.get_tenant_ai_settings("t1")
    assert loaded is not None and loaded.rag is not None and loaded.rag.model == "rag-model"
    assert loaded.no_egress is False
    assert loaded.pipeline is None  # partial: unset purposes stay unset
    repo.delete_tenant_ai_settings("t1")
    assert repo.get_tenant_ai_settings("t1") is None


# --- per-tenant OpenAI API key (#719) ---


def test_tenant_openai_key_store_round_trip_and_delete() -> None:
    repo = InMemoryAppSettingsRepository()
    assert repo.get_tenant_openai_api_key("t1") == ""
    repo.set_tenant_openai_api_key("t1", "sk-t1")
    assert repo.get_tenant_openai_api_key("t1") == "sk-t1"
    assert repo.get_tenant_openai_api_key("t2") == ""  # tenant isolation
    # Resetting the tenant's override drops the tenant key too (back to the layers below).
    repo.delete_tenant_ai_settings("t1")
    assert repo.get_tenant_openai_api_key("t1") == ""


def test_openai_key_resolution_tenant_then_global_then_env() -> None:
    repo = InMemoryAppSettingsRepository()
    env = _env(openai_api_key="sk-env")
    assert effective_openai_api_key(repo, "t1", env) == "sk-env"
    repo.set_openai_api_key("sk-global")
    assert effective_openai_api_key(repo, "t1", env) == "sk-global"
    repo.set_tenant_openai_api_key("t1", "sk-tenant")
    assert effective_openai_api_key(repo, "t1", env) == "sk-tenant"
    assert effective_openai_api_key(repo, "t2", env) == "sk-global"  # other tenants unaffected


def test_openai_key_resolution_empty_when_nothing_set() -> None:
    repo = InMemoryAppSettingsRepository()
    assert effective_openai_api_key(repo, "t1", _env()) == ""
