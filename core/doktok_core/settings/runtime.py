"""Runtime helpers derived from the AI configuration (M16 #374)."""

from __future__ import annotations

from doktok_contracts.schemas import AiSettings


def local_ollama_needed(ai: AiSettings, *, default_url: str, ocr_engine: str) -> bool:
    """Whether the in-stack (default-URL) Ollama is referenced by any active purpose.

    False means every Ollama consumer is offloaded (remote URL or OpenAI) and OCR is not the Ollama
    vision engine, so the local Ollama container can be stopped to reclaim its memory. The embedder
    always uses Ollama, so a default (non-overridden) embedding URL alone keeps it needed.
    """

    def is_local(url: str | None) -> bool:
        return (url or default_url) == default_url

    if is_local(ai.embedding.ollama_base_url):
        return True
    if ai.pipeline.provider == "ollama" and is_local(ai.pipeline.ollama_base_url):
        return True
    if ai.rag.provider == "ollama" and is_local(ai.rag.ollama_base_url):
        return True
    # The Ollama vision OCR engine (non-"paddleocr") talks to the default Ollama too.
    return ocr_engine != "paddleocr"
