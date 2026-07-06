"""Chat/completion via OpenAI (used for RAG answering + reranking when selected in Settings)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from doktok_contracts.media import AgentMessage, ChatChunk, LlmToolCall, LlmUsage, ToolCallTurn

from doktok_provider_openai.client import (
    openai_chat_with_tools,
    openai_chat_with_usage,
    openai_stream_responses,
)

logger = logging.getLogger("doktok.provider.openai")


def _to_openai_message(msg: AgentMessage) -> dict[str, Any]:
    """Map a contract AgentMessage to an OpenAI chat message."""
    if msg.role == "tool":
        return {"role": "tool", "tool_call_id": msg.tool_call_id or "", "content": msg.content}
    out: dict[str, Any] = {"role": msg.role, "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in msg.tool_calls
        ]
    return out


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
        """Stream answer and reasoning-summary chunks via the OpenAI Responses API.

        Reasoning summary deltas (``kind="reasoning"``) are emitted when ``reasoning_effort`` is
        set and the model supports it; otherwise only ``kind="answer"`` chunks arrive.  ``think``
        is accepted for interface parity with the Ollama adapter but does not alter
        ``reasoning_effort`` (that is fixed at construction time).

        Falls back to a single non-streaming answer chunk if the Responses API is unavailable or
        returns an error so the chat path never breaks.
        """
        _ = think
        usage_out: list[dict[str, Any]] = []
        t0 = time.monotonic()
        try:
            for kind, text in openai_stream_responses(
                api_key=self._api_key,
                base_url=self._base_url,
                model=self._model,
                system="You are a careful assistant. Follow the user's instructions exactly.",
                user=prompt,
                timeout=self._timeout,
                reasoning_effort=self._reasoning_effort,
                _usage_out=usage_out,
            ):
                yield ChatChunk(kind=kind, text=text)
        except Exception:
            logger.warning(
                "OpenAI Responses API stream failed; falling back to non-streaming complete()",
                exc_info=True,
            )
            yield ChatChunk(kind="answer", text=self.complete(prompt))
            return
        if usage_out:
            wall_ms = round((time.monotonic() - t0) * 1000)
            u = usage_out[0]
            details: dict[str, Any] = u.get("output_tokens_details") or {}
            reasoning_tokens = int(details.get("reasoning_tokens") or 0)
            output_tokens = int(u.get("output_tokens") or 0)
            self._last_usage = LlmUsage(
                prompt_tokens=int(u.get("input_tokens") or 0),
                answer_tokens=max(0, output_tokens - reasoning_tokens),
                reasoning_tokens=reasoning_tokens,
                wall_ms=wall_ms,
                estimated=not u,
            )

    def chat_with_tools(
        self, messages: list[AgentMessage], tools: list[dict[str, Any]]
    ) -> ToolCallTurn:
        message, usage = openai_chat_with_tools(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            messages=[_to_openai_message(m) for m in messages],
            tools=tools,
            timeout=self._timeout,
            reasoning_effort=self._reasoning_effort,
        )
        self._last_usage = usage
        text = str(message.get("content") or "")
        calls: list[LlmToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                LlmToolCall(
                    id=str(raw.get("id") or fn.get("name", "")),
                    name=fn.get("name", ""),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        return ToolCallTurn(text=text, tool_calls=calls, usage=usage)
