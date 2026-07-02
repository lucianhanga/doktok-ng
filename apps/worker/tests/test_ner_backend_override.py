"""Per-purpose NER/KEG backend resolution (ADR-0023): local span model, LLM, or safe fallback."""

from __future__ import annotations

import sys
import types

import pytest
from doktok_contracts.media import ExtractedEntity, ExtractedRelation
from doktok_contracts.schemas import AiPurposeSettings
from doktok_core.security.egress import EgressBlocked
from doktok_provider_ollama import OllamaEntityNerExtractor, OllamaRelationExtractor
from doktok_provider_openai import OpenAiEntityNerExtractor, OpenAiRelationExtractor
from doktok_worker.composition import _resolve_ner_backend, _resolve_relation_backend


class _NerFallback:
    def extract(self, text: str) -> list[ExtractedEntity]:  # noqa: ARG002
        return []


class _RelFallback:
    def extract(  # noqa: ARG002
        self, text: str, entity_list: list[tuple[str, str]]
    ) -> list[ExtractedRelation]:
        return []


def _cfg(provider: str, model: str, *, num_ctx: int = 8192) -> AiPurposeSettings:
    return AiPurposeSettings(provider=provider, model=model, num_ctx=num_ctx)


def _ner(
    cfg: AiPurposeSettings,
    fallback: _NerFallback,
    *,
    key: str = "sk-test",
    no_egress: bool = False,
) -> tuple[object, str]:
    return _resolve_ner_backend(
        cfg,
        fallback,
        key=key,
        no_egress=no_egress,
        default_url="http://localhost:11434",
        timeout=120.0,
        keep_alive="30m",
    )


def _keg(
    cfg: AiPurposeSettings,
    fallback: _RelFallback,
    *,
    key: str = "sk-test",
    no_egress: bool = False,
) -> tuple[object, str]:
    return _resolve_relation_backend(
        cfg,
        fallback,
        key=key,
        no_egress=no_egress,
        default_url="http://localhost:11434",
        timeout=120.0,
        keep_alive="30m",
    )


def _fake_gliner(factory: object) -> types.ModuleType:
    mod = types.ModuleType("doktok_provider_gliner")
    mod.GlinerEntityNerExtractor = factory  # type: ignore[attr-defined]
    mod.NuNerEntityNerExtractor = factory  # type: ignore[attr-defined]
    mod.GlinerRelexRelationExtractor = factory  # type: ignore[attr-defined]
    return mod


# --------------------------------------------------------------------------- NER


def test_ner_openai_builds_llm_extractor() -> None:
    ext, token = _ner(_cfg("openai", "gpt-4o-mini"), _NerFallback())
    assert isinstance(ext, OpenAiEntityNerExtractor)
    assert token == "openai:gpt-4o-mini"


def test_ner_openai_blocked_under_no_egress() -> None:
    ext, token = _ner(_cfg("openai", "gpt-4o-mini"), _NerFallback(), no_egress=True)
    assert isinstance(ext, EgressBlocked) and token == "openai:blocked"


def test_ner_openai_blocked_without_key() -> None:
    ext, _ = _ner(_cfg("openai", "gpt-4o-mini"), _NerFallback(), key="")
    assert isinstance(ext, EgressBlocked)


def test_ner_ollama_builds_local_llm() -> None:
    ext, token = _ner(_cfg("ollama", "qwen3.6:27b"), _NerFallback())
    assert isinstance(ext, OllamaEntityNerExtractor)
    assert token.startswith("ollama:qwen3.6:27b")


def test_ner_gliner_missing_runtime_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gliner engine extra (torch) is optional and the adapter imports it lazily at extract time,
    # so a missing runtime must be caught at resolve time via the importability probe -- not at
    # construction, which never touches gliner. Simulate the probe reporting the runtime absent.
    monkeypatch.setattr("doktok_worker.composition._gliner_runtime_available", lambda: False)
    fallback = _NerFallback()
    ext, token = _ner(_cfg("gliner", "gliner-community/gliner_large-v2.5"), fallback)
    assert ext is fallback and token == "gliner-fallback"


def test_ner_gliner_loads_local(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = _NerFallback()
    monkeypatch.setattr("doktok_worker.composition._gliner_runtime_available", lambda: True)
    monkeypatch.setitem(sys.modules, "doktok_provider_gliner", _fake_gliner(lambda *a, **k: chosen))
    ext, token = _ner(_cfg("gliner", "gliner-community/gliner_large-v2.5"), _NerFallback())
    assert ext is chosen and token.startswith("gliner:gliner-community/gliner_large-v2.5")


# --------------------------------------------------------------------------- relations (KEG)


def test_keg_gliner_relex_loads_local(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = _RelFallback()
    monkeypatch.setattr("doktok_worker.composition._gliner_runtime_available", lambda: True)
    monkeypatch.setitem(sys.modules, "doktok_provider_gliner", _fake_gliner(lambda *a, **k: chosen))
    ext, token = _keg(_cfg("gliner-relex", "knowledgator/gliner-relex-large-v1.0"), _RelFallback())
    assert ext is chosen and token.startswith("gliner-relex:")


def test_keg_gliner_relex_missing_runtime_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # See test_ner_gliner_missing_runtime_falls_back: the import is deferred to extract time, so the
    # resolver must fall back on a missing runtime reported by the probe, not a construction raise.
    monkeypatch.setattr("doktok_worker.composition._gliner_runtime_available", lambda: False)
    fallback = _RelFallback()
    ext, token = _keg(_cfg("gliner-relex", "knowledgator/gliner-relex-large-v1.0"), fallback)
    assert ext is fallback and token == "gliner-relex-fallback"


def test_keg_openai_builds_llm_extractor() -> None:
    ext, token = _keg(_cfg("openai", "gpt-4o-mini"), _RelFallback())
    assert isinstance(ext, OpenAiRelationExtractor) and token == "openai:gpt-4o-mini"


def test_keg_openai_blocked_under_no_egress() -> None:
    ext, _ = _keg(_cfg("openai", "gpt-4o-mini"), _RelFallback(), no_egress=True)
    assert isinstance(ext, EgressBlocked)


def test_keg_ollama_builds_local_llm() -> None:
    ext, token = _keg(_cfg("ollama", "qwen3.6:27b"), _RelFallback())
    assert isinstance(ext, OllamaRelationExtractor) and token.startswith("ollama:")
