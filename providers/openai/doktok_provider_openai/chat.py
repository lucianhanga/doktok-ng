"""Chat/completion via OpenAI (used for RAG answering + reranking when selected in Settings)."""

from __future__ import annotations

from collections.abc import Iterator

from doktok_contracts.media import ChatChunk, LlmUsage

from doktok_provider_openai.client import openai_chat_with_usage


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
        self._last_usage: LlmUsage | None = None

    def get_last_usage(self) -> LlmUsage | None:
        return self._last_usage

    def complete(self, prompt: str) -> str:
        content, usage = openai_chat_with_usage(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            system="You are a careful assistant. Follow the user's instructions exactly.",
            user=prompt,
            timeout=self._timeout,
            reasoning_effort=self._reasoning_effort,
        )
        self._last_usage = usage
        return content.strip()

    def stream_complete(self, prompt: str, *, think: bool | None = None) -> Iterator[ChatChunk]:
        # No token streaming for OpenAI here (and chat-completions exposes no reasoning); emit the
        # full answer as a single chunk so the streaming UI still works (degrades gracefully).
        _ = think
        yield ChatChunk(kind="answer", text=self.complete(prompt))
