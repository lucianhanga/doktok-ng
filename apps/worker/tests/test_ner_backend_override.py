"""The opt-in DOKTOK_NER_BACKEND override: selection, token, and safe fallback to the LLM NER."""

from __future__ import annotations

import sys
import types

import pytest
from doktok_contracts.media import ExtractedEntity, ExtractedRelation
from doktok_worker.composition import _ner_backend_override, _relation_backend_override


class _Sentinel:
    """Stands in for an EntityNerExtractor."""

    def extract(self, text: str) -> list[ExtractedEntity]:  # noqa: ARG002
        return []


class _RelSentinel:
    """Stands in for a RelationExtractor."""

    def extract(  # noqa: ARG002
        self, text: str, entity_list: list[tuple[str, str]]
    ) -> list[ExtractedRelation]:
        return []


def _fake_provider(factory: object) -> types.ModuleType:
    mod = types.ModuleType("doktok_provider_gliner")
    mod.GlinerEntityNerExtractor = factory  # type: ignore[attr-defined]
    mod.NuNerEntityNerExtractor = factory  # type: ignore[attr-defined]
    mod.GlinerRelexRelationExtractor = factory  # type: ignore[attr-defined]
    return mod


def test_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOKTOK_NER_BACKEND", raising=False)
    default = _Sentinel()
    extractor, token = _ner_backend_override(default)
    assert extractor is default and token is None


def test_unknown_backend_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_NER_BACKEND", "spacy")
    default = _Sentinel()
    extractor, token = _ner_backend_override(default)
    assert extractor is default and token is None


def test_runtime_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_NER_BACKEND", "gliner")

    def boom(*_args: object, **_kwargs: object) -> _Sentinel:
        raise RuntimeError("gliner runtime not installed")

    monkeypatch.setitem(sys.modules, "doktok_provider_gliner", _fake_provider(boom))
    default = _Sentinel()
    extractor, token = _ner_backend_override(default)
    assert extractor is default and token is None


def test_loaded_backend_is_used_and_tokenised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_NER_BACKEND", "nuner")
    monkeypatch.setenv("DOKTOK_NER_DEVICE", "cuda")
    chosen = _Sentinel()
    monkeypatch.setitem(
        sys.modules, "doktok_provider_gliner", _fake_provider(lambda *a, **k: chosen)
    )
    default = _Sentinel()
    extractor, token = _ner_backend_override(default)
    assert extractor is chosen
    assert token is not None and token.startswith("nuner:") and token.endswith(":cuda")


def test_relation_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOKTOK_REL_BACKEND", raising=False)
    default = _RelSentinel()
    extractor, token = _relation_backend_override(default)
    assert extractor is default and token is None


def test_relation_runtime_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_REL_BACKEND", "gliner-relex")

    def boom(*_args: object, **_kwargs: object) -> _Sentinel:
        raise RuntimeError("gliner runtime not installed")

    monkeypatch.setitem(sys.modules, "doktok_provider_gliner", _fake_provider(boom))
    default = _RelSentinel()
    extractor, token = _relation_backend_override(default)
    assert extractor is default and token is None


def test_relation_loaded_backend_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKTOK_REL_BACKEND", "gliner-relex")
    chosen = _RelSentinel()
    monkeypatch.setitem(
        sys.modules, "doktok_provider_gliner", _fake_provider(lambda *a, **k: chosen)
    )
    default = _RelSentinel()
    extractor, token = _relation_backend_override(default)
    assert extractor is chosen
    assert token is not None and token.startswith("gliner-relex:")
