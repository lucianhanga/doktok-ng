import pytest
from doktok_core.config import Settings
from doktok_core.registry import PortNotRegistered, Registry, build_registry


def _settings_without_env_file() -> Settings:
    # Ignore any local .env so defaults are deterministic in tests.
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_defaults_are_local_first() -> None:
    settings = _settings_without_env_file()

    assert settings.env == "local"
    assert settings.no_egress is True
    assert settings.default_model == "qwen3.6:35b-a3b"
    assert settings.embedding_model == "qwen3-embedding:0.6b"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.max_file_mb == 200
    assert settings.file_stability_seconds == 3


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_ENV", "staging")
    monkeypatch.setenv("DOKTOK_NO_EGRESS", "false")

    settings = _settings_without_env_file()

    assert settings.env == "staging"
    assert settings.no_egress is False


def test_registry_is_empty_at_m0() -> None:
    registry = build_registry()
    assert isinstance(registry, Registry)

    class SomePort:  # pragma: no cover - simple marker
        ...

    assert registry.is_registered(SomePort) is False
    with pytest.raises(PortNotRegistered):
        registry.resolve(SomePort)
