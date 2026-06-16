"""Headless AI-settings bootstrap (APP-2): seed-if-absent from env."""

from __future__ import annotations

from doktok_core.config import Settings
from doktok_core.settings.bootstrap import seed_ai_settings
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository


def _settings(
    *,
    pipeline_provider: str = "",
    pipeline_model: str = "",
    rag_provider: str = "",
    rag_model: str = "",
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        no_egress=False,
        pipeline_provider=pipeline_provider,
        pipeline_model=pipeline_model,
        rag_provider=rag_provider,
        rag_model=rag_model,
    )


def test_seeds_hybrid_split_on_fresh_repo() -> None:
    repo = InMemoryAppSettingsRepository()
    settings = _settings(
        pipeline_provider="openai",
        pipeline_model="gpt-4o-mini",
        rag_provider="openai",
        rag_model="gpt-4o-mini",
    )

    assert seed_ai_settings(repo, settings) is True
    ai = repo.get_ai_settings()
    assert ai.pipeline.provider == "openai" and ai.pipeline.model == "gpt-4o-mini"
    assert ai.rag.provider == "openai" and ai.rag.model == "gpt-4o-mini"


def test_model_defaults_from_catalog_when_omitted() -> None:
    repo = InMemoryAppSettingsRepository()
    # Provider only -> the catalog's first option for that provider supplies model + context.
    assert seed_ai_settings(repo, _settings(rag_provider="openai")) is True
    rag = repo.get_ai_settings().rag
    assert rag.provider == "openai" and rag.model and rag.num_ctx > 0


def test_noop_when_already_saved() -> None:
    repo = InMemoryAppSettingsRepository()
    repo.set_ai_settings(repo.get_ai_settings())  # operator already saved (defaults here)

    assert seed_ai_settings(repo, _settings(pipeline_provider="openai")) is False
    assert repo.get_ai_settings().pipeline.provider == "ollama"  # not overwritten


def test_noop_when_no_env_requested() -> None:
    repo = InMemoryAppSettingsRepository()
    assert seed_ai_settings(repo, _settings()) is False
    assert repo.has_ai_settings() is False
