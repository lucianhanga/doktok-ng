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


def test_openai_api_key_defaults_empty_and_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _settings_without_env_file().openai_api_key == ""

    monkeypatch.setenv("DOKTOK_OPENAI_API_KEY", "sk-test-123")
    assert _settings_without_env_file().openai_api_key == "sk-test-123"


def test_registry_is_empty_at_m0() -> None:
    registry = build_registry()
    assert isinstance(registry, Registry)

    class SomePort:  # pragma: no cover - simple marker
        ...

    assert registry.is_registered(SomePort) is False
    with pytest.raises(PortNotRegistered):
        registry.resolve(SomePort)


def test_no_egress_rejects_remote_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_OLLAMA_BASE_URL", "http://10.0.0.5:11434")
    with pytest.raises(ValueError, match="NO_EGRESS"):
        _settings_without_env_file()


def test_no_egress_allows_remote_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_OLLAMA_BASE_URL", "http://10.0.0.5:11434")
    monkeypatch.setenv("DOKTOK_NO_EGRESS", "false")
    settings = _settings_without_env_file()
    assert settings.no_egress is False and "10.0.0.5" in settings.ollama_base_url


def test_openai_egress_allowed_requires_key_and_no_egress_off() -> None:
    from doktok_core.security.egress import openai_egress_allowed

    # Usable only when a key is set AND egress is permitted (no_egress=False).
    assert openai_egress_allowed(key="sk-x", no_egress=False) is True
    # No-egress on => refuse even with a key (the security gate, APP-3).
    assert openai_egress_allowed(key="sk-x", no_egress=True) is False
    # No key => never usable, regardless of egress.
    assert openai_egress_allowed(key="", no_egress=False) is False
    assert openai_egress_allowed(key="", no_egress=True) is False


def test_loopback_url_detection() -> None:
    from doktok_core.security.egress import is_loopback_url

    assert is_loopback_url("http://localhost:11434")
    assert is_loopback_url("http://127.0.0.1:11434")
    assert is_loopback_url("http://[::1]:11434")
    assert not is_loopback_url("http://10.0.0.5:11434")
    assert not is_loopback_url("https://api.example.com")
