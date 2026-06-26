"""local_ollama_needed: when the in-stack Ollama container can be stopped (M16 #374)."""

from __future__ import annotations

from doktok_contracts.schemas import AiEmbeddingSettings, AiPurposeSettings, AiSettings
from doktok_core.settings.runtime import local_ollama_needed

DEFAULT = "http://ollama:11434"
REMOTE = "http://10.0.0.22:11434"


def _ai(
    *, pipeline: AiPurposeSettings, rag: AiPurposeSettings, embedding_url: str | None
) -> AiSettings:
    return AiSettings(
        pipeline=pipeline, rag=rag, embedding=AiEmbeddingSettings(ollama_base_url=embedding_url)
    )


def _openai(model: str = "gpt-4o-mini") -> AiPurposeSettings:
    return AiPurposeSettings(provider="openai", model=model, num_ctx=8192)


def _ollama(url: str | None) -> AiPurposeSettings:
    return AiPurposeSettings(
        provider="ollama", model="qwen3.6:35b-a3b", num_ctx=8192, ollama_base_url=url
    )


def test_default_embedding_keeps_local_ollama_needed() -> None:
    ai = _ai(pipeline=_openai(), rag=_openai(), embedding_url=None)
    assert local_ollama_needed(ai, default_url=DEFAULT, ocr_engine="paddleocr") is True


def test_fully_offloaded_means_local_ollama_not_needed() -> None:
    ai = _ai(pipeline=_openai(), rag=_openai(), embedding_url=REMOTE)
    assert local_ollama_needed(ai, default_url=DEFAULT, ocr_engine="paddleocr") is False


def test_local_pipeline_keeps_it_needed_even_with_remote_embedding() -> None:
    ai = _ai(pipeline=_ollama(None), rag=_openai(), embedding_url=REMOTE)
    assert local_ollama_needed(ai, default_url=DEFAULT, ocr_engine="paddleocr") is True


def test_remote_pipeline_does_not_keep_it_needed() -> None:
    ai = _ai(pipeline=_ollama(REMOTE), rag=_openai(), embedding_url=REMOTE)
    assert local_ollama_needed(ai, default_url=DEFAULT, ocr_engine="paddleocr") is False


def test_ollama_vision_ocr_keeps_it_needed() -> None:
    ai = _ai(pipeline=_openai(), rag=_openai(), embedding_url=REMOTE)
    assert local_ollama_needed(ai, default_url=DEFAULT, ocr_engine="glm-ocr") is True
