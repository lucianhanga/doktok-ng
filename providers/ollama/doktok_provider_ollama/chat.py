"""Chat/completion via a local Ollama model (DOKTOK_DEFAULT_MODEL).

Talks only to the local Ollama endpoint (no external egress). Used for the OCR-vs-embedded text
judge (M5.x) and RAG answering (M6).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
from doktok_contracts.media import (
    AgentMessage,
    ChatChunk,
    LlmToolCall,
    LlmUsage,
    ToolCallTurn,
)


def _to_ollama_message(msg: AgentMessage) -> dict[str, Any]:
    """Map a contract AgentMessage to an Ollama /api/chat message."""
    out: dict[str, Any] = {"role": msg.role, "content": msg.content}
    if msg.role == "tool" and msg.name:
        out["tool_name"] = msg.name
    if msg.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in msg.tool_calls
        ]
    return out


def _split_tokens(eval_count: int, reasoning_chars: int, answer_chars: int) -> tuple[int, int]:
    """Split Ollama's combined ``eval_count`` into (reasoning, answer) tokens by output char ratio.
    Ollama does not report the split, so this is an estimate that always sums to ``eval_count``."""
    total = reasoning_chars + answer_chars
    if total <= 0 or eval_count <= 0:
        return 0, max(0, eval_count)
    reasoning = round(eval_count * reasoning_chars / total)
    return reasoning, eval_count - reasoning


def _est_tokens(chars: int) -> int:
    """Character-based token estimate (~3.5 chars/token) when no provider counter is available."""
    return max(0, round(chars / 3.5))


def _as_int(value: object) -> int:
    """Coerce an Ollama JSON field (typed ``object``) to int; 0 when absent or non-numeric."""
    return value if isinstance(value, int) else 0


class OllamaChatModelProvider:
    """``ChatModelProvider`` backed by Ollama's ``/api/generate`` endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str,
        *,
        timeout: float = 600.0,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        keep_alive: str | None = None,
        think: bool = False,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._num_ctx = num_ctx
        # Cap output for short-response callers (e.g. the listwise reranker emits a tiny array).
        self._num_predict = num_predict
        # Residency hint: keep the (large) RAG model warm so idle gaps don't trigger a cold reload.
        self._keep_alive = keep_alive
        # Whether the model reasons before answering (reasoning density off -> False). No structured
        # `format` here, so toggling think is always safe.
        self._think = think
        # Token/timing of the most recent call (M8); read via get_last_usage() after the call.
        self._last_usage: LlmUsage | None = None

    def get_last_usage(self) -> LlmUsage | None:
        return self._last_usage

    def complete(self, prompt: str) -> str:
        options: dict[str, object] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict
        payload: dict[str, object] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "think": self._think,
            "options": options,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        t0 = time.monotonic()
        response = httpx.post(f"{self._base_url}/api/generate", json=payload, timeout=self._timeout)
        response.raise_for_status()
        wall_ms = round((time.monotonic() - t0) * 1000)
        body = response.json()
        text = str(body.get("response", "")).strip()
        eval_count = body.get("eval_count")
        eval_ns = body.get("eval_duration")
        self._last_usage = LlmUsage(
            prompt_tokens=_as_int(body.get("prompt_eval_count")),
            answer_tokens=_as_int(eval_count) if eval_count is not None else _est_tokens(len(text)),
            reasoning_tokens=0,  # /api/generate is non-streaming; reasoning isn't separable here
            wall_ms=wall_ms,
            eval_ms=round(_as_int(eval_ns) / 1_000_000) if eval_ns else None,
            estimated=eval_count is None,
        )
        return text

    def stream_complete(self, prompt: str, *, think: bool | None = None) -> Iterator[ChatChunk]:
        """Stream the answer via /api/chat (NDJSON). Reasoning tokens (when reasoning is on) arrive
        in the message's ``thinking`` field, answer tokens in ``content`` - yielded as distinct
        chunks.
        ``think=None`` uses the configured reasoning (``self._think``, from settings); True/False
        overrides it for this call (e.g. the chat 'Show reasoning' toggle)."""
        effective_think = self._think if think is None else think
        options: dict[str, object] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        if self._num_predict is not None:
            options["num_predict"] = self._num_predict
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "think": effective_think,
            "options": options,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        self._last_usage = None
        reasoning_chars = 0
        answer_chars = 0
        done: dict[str, object] = {}
        t0 = time.monotonic()
        with httpx.stream(
            "POST", f"{self._base_url}/api/chat", json=payload, timeout=self._timeout
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                obj = json.loads(line)
                message = obj.get("message") or {}
                reasoning = message.get("thinking")
                if reasoning:
                    reasoning_chars += len(reasoning)
                    yield ChatChunk(kind="reasoning", text=reasoning)
                content = message.get("content")
                if content:
                    answer_chars += len(content)
                    yield ChatChunk(kind="answer", text=content)
                if obj.get("done"):
                    done = obj
        wall_ms = round((time.monotonic() - t0) * 1000)
        eval_count = done.get("eval_count")
        eval_ns = done.get("eval_duration")
        if eval_count is not None:
            reasoning_tokens, answer_tokens = _split_tokens(
                _as_int(eval_count), reasoning_chars, answer_chars
            )
        else:
            reasoning_tokens = _est_tokens(reasoning_chars)
            answer_tokens = _est_tokens(answer_chars)
        self._last_usage = LlmUsage(
            prompt_tokens=_as_int(done.get("prompt_eval_count")),
            answer_tokens=answer_tokens,
            reasoning_tokens=reasoning_tokens,
            wall_ms=wall_ms,
            eval_ms=round(_as_int(eval_ns) / 1_000_000) if eval_ns else None,
            estimated=eval_count is None,
        )

    def chat_with_tools(
        self, messages: list[AgentMessage], tools: list[dict[str, Any]]
    ) -> ToolCallTurn:
        """One tool-calling turn via /api/chat (non-streaming). ``think`` is forced off: reasoning
        plus tool-calling is unreliable on the local MoE, and the loop needs clean tool calls."""
        options: dict[str, object] = {"temperature": 0}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [_to_ollama_message(m) for m in messages],
            "tools": [{"type": "function", "function": spec} for spec in tools],
            "stream": False,
            "think": False,
            "options": options,
        }
        if self._keep_alive is not None:
            payload["keep_alive"] = self._keep_alive
        response = httpx.post(f"{self._base_url}/api/chat", json=payload, timeout=self._timeout)
        response.raise_for_status()
        message = response.json().get("message") or {}
        calls: list[LlmToolCall] = []
        for i, raw in enumerate(message.get("tool_calls") or []):
            fn = raw.get("function", {})
            args = fn.get("arguments")
            calls.append(
                LlmToolCall(
                    id=str(fn.get("name", "")) + f":{i}",
                    name=str(fn.get("name", "")),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        return ToolCallTurn(text=str(message.get("content") or ""), tool_calls=calls)
