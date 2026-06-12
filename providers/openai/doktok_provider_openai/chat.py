"""Chat/completion via OpenAI (used for RAG answering + reranking when selected in Settings)."""

from __future__ import annotations

from doktok_provider_openai.client import openai_chat


class OpenAiChatModelProvider:
    """``ChatModelProvider`` backed by OpenAI's chat completions endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._reasoning_effort = reasoning_effort

    def complete(self, prompt: str) -> str:
        return openai_chat(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            system="You are a careful assistant. Follow the user's instructions exactly.",
            user=prompt,
            timeout=self._timeout,
            reasoning_effort=self._reasoning_effort,
        ).strip()
