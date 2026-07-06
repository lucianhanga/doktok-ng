"""Shared OpenAI Chat Completions client (httpx; no SDK dependency).

Reasoning models (gpt-5*, o-series) take ``reasoning_effort`` and reject a custom temperature; other
models take ``temperature``. JSON output is requested with a (non-strict) ``json_schema`` response
format so the lenient core parsers can validate/normalize the result.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
from doktok_contracts.media import AgentMessage, LlmUsage

logger = logging.getLogger("doktok.provider.openai")

_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds; doubled per attempt, plus jitter


def _max_concurrency() -> int:
    """Process-wide ceiling on concurrent OpenAI requests (DOKTOK_OPENAI_MAX_CONCURRENCY, default
    5). This is the single 429 guard: the reconciler fan-out, the OCR-quality judge, and RAG all go
    through this client, so without one cap they sum past the account's rate limit. Set it to match
    your OpenAI tier's RPM/TPM."""
    try:
        return max(1, int(os.environ.get("DOKTOK_OPENAI_MAX_CONCURRENCY", "5")))
    except ValueError:
        return 5


# A bounded semaphore (not per-instance) so ALL OpenAI callers in this process share one ceiling.
# A request holds its slot for the whole retry+backoff loop, so retries can't stampede past the cap.
_REQUEST_SEMAPHORE = threading.BoundedSemaphore(_max_concurrency())


class OpenAiError(RuntimeError):
    """An OpenAI request failed. Base class for the classified errors below."""


class OpenAiAuthError(OpenAiError):
    """Authentication/authorization failed (HTTP 401/403) - a bad or missing key. Not retryable."""


class OpenAiRateLimitError(OpenAiError):
    """Rate limited (HTTP 429) and still failing after retries."""


class OpenAiTimeoutError(OpenAiError):
    """The request timed out."""


class OpenAiServerError(OpenAiError):
    """OpenAI returned a 5xx after retries."""


def _backoff(attempt: int) -> float:
    return float(_BACKOFF_BASE * (2**attempt) + random.uniform(0, 0.25))


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)  # seconds form; HTTP-date form falls back to plain backoff
    except ValueError:
        return None


def _post_with_retry(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """POST with bounded exponential backoff + jitter, honoring Retry-After. Retries 429/5xx and
    timeouts; fails fast on auth errors; raises a classified ``OpenAiError`` when out of retries."""
    last_error: OpenAiError = OpenAiError("OpenAI request failed")
    # Hold a global concurrency slot for the whole attempt loop so the process never has more than
    # DOKTOK_OPENAI_MAX_CONCURRENCY requests (incl. their backoff waits) in flight at once.
    with _REQUEST_SEMAPHORE:
        return _post_with_retry_locked(
            url=url,
            payload=payload,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
            last_error=last_error,
        )


def _post_with_retry_locked(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
    last_error: OpenAiError,
) -> httpx.Response:
    for attempt in range(max_retries + 1):
        delay: float | None = None
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            last_error = OpenAiTimeoutError(f"OpenAI request timed out after {timeout}s")
            delay = _backoff(attempt)
        except httpx.HTTPError as exc:
            last_error = OpenAiError(f"OpenAI request failed: {exc}")
            delay = _backoff(attempt)
        else:
            status = response.status_code
            if status < 400:
                return response
            if status in (401, 403):
                raise OpenAiAuthError(
                    f"OpenAI authentication failed (HTTP {status}); check the API key"
                )
            if status == 429:
                last_error = OpenAiRateLimitError("OpenAI rate limit exceeded (HTTP 429)")
                delay = _retry_after(response) or _backoff(attempt)
            elif status >= 500:
                last_error = OpenAiServerError(f"OpenAI server error (HTTP {status})")
                delay = _backoff(attempt)
            else:
                raise OpenAiError(f"OpenAI request failed (HTTP {status}): {response.text[:200]}")
        if attempt >= max_retries or delay is None:
            break
        logger.warning(
            "%s; retrying in %.1fs (attempt %d/%d)", last_error, delay, attempt + 1, max_retries
        )
        time.sleep(delay)
    raise last_error


def openai_chat(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float = 120.0,
    json_schema: dict[str, Any] | None = None,
    schema_name: str = "result",
    reasoning_effort: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort  # reasoning models reject temperature
    else:
        payload["temperature"] = 0
    if json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema, "strict": False},
        }
    response = _post_with_retry(
        url=f"{base_url.rstrip('/')}/chat/completions",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    choices = response.json().get("choices", [])
    if not choices:
        return ""
    return str(choices[0].get("message", {}).get("content") or "")


def _usage_from_body(body: dict[str, Any], wall_ms: int) -> LlmUsage:
    """Build LlmUsage from an OpenAI response body's ``usage`` block (exact when present)."""
    usage_obj = body.get("usage") or {}
    details = usage_obj.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens") or 0)
    completion = int(usage_obj.get("completion_tokens") or 0)
    return LlmUsage(
        prompt_tokens=int(usage_obj.get("prompt_tokens") or 0),
        answer_tokens=max(0, completion - reasoning),
        reasoning_tokens=reasoning,
        wall_ms=wall_ms,
        estimated=not usage_obj,
    )


def openai_chat_with_tools(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout: float = 120.0,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, Any], LlmUsage]:
    """One provider-native tool-calling turn. ``messages`` are already OpenAI-shaped; ``tools`` are
    JSON function specs. Returns the raw assistant ``message`` dict (``content`` and/or
    ``tool_calls``) plus token/timing usage. The caller maps it to the contract ``ToolCallTurn``."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": [{"type": "function", "function": spec} for spec in tools],
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["temperature"] = 0
    t0 = time.monotonic()
    response = _post_with_retry(
        url=f"{base_url.rstrip('/')}/chat/completions",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    wall_ms = round((time.monotonic() - t0) * 1000)
    body = response.json()
    choices = body.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    return (message if isinstance(message, dict) else {}), _usage_from_body(body, wall_ms)


def openai_chat_with_usage(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float = 120.0,
    reasoning_effort: str | None = None,
) -> tuple[str, LlmUsage]:
    """Like ``openai_chat`` but also returns token/timing usage (M8). Reasoning models report exact
    reasoning tokens via ``usage.completion_tokens_details.reasoning_tokens``."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    else:
        payload["temperature"] = 0
    t0 = time.monotonic()
    response = _post_with_retry(
        url=f"{base_url.rstrip('/')}/chat/completions",
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    wall_ms = round((time.monotonic() - t0) * 1000)
    body = response.json()
    choices = body.get("choices", [])
    content = str(choices[0].get("message", {}).get("content") or "") if choices else ""
    usage_obj = body.get("usage") or {}
    details = usage_obj.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens") or 0)
    completion = int(usage_obj.get("completion_tokens") or 0)
    usage = LlmUsage(
        prompt_tokens=int(usage_obj.get("prompt_tokens") or 0),
        answer_tokens=max(0, completion - reasoning),
        reasoning_tokens=reasoning,
        wall_ms=wall_ms,
        estimated=not usage_obj,
    )
    return content, usage


def _execute_responses_stream(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    _usage_out: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[str, str]]:
    """Execute a pre-built Responses API streaming request and yield ``(kind, text)`` tuples.

    Holds the process-wide concurrency semaphore for the full duration of the stream.  Raises a
    classified ``OpenAiError`` subclass on HTTP errors; yields ``("reasoning", delta)`` and
    ``("answer", delta)`` for content events; appends the raw ``usage`` dict to ``_usage_out``
    (when provided) on ``response.completed``.  Not intended to be called directly - use
    ``openai_stream_responses`` or ``openai_stream_responses_messages`` instead.
    """
    with (
        _REQUEST_SEMAPHORE,
        httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp,
    ):
        status = resp.status_code
        if status in (401, 403):
            raise OpenAiAuthError(
                f"OpenAI authentication failed (HTTP {status}); check the API key"
            )
        if status == 429:
            raise OpenAiRateLimitError("OpenAI rate limit exceeded (HTTP 429)")
        if status >= 500:
            raise OpenAiServerError(f"OpenAI server error (HTTP {status})")
        if status >= 400:
            raise OpenAiError(f"OpenAI Responses API failed (HTTP {status})")
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]  # strip leading "data: "
            if data == "[DONE]":
                break
            try:
                event: dict[str, Any] = json.loads(data)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type") or ""
            if event_type == "response.reasoning_summary_text.delta":
                delta: str = event.get("delta") or ""
                if delta:
                    yield ("reasoning", delta)
            elif event_type == "response.output_text.delta":
                delta = event.get("delta") or ""
                if delta:
                    yield ("answer", delta)
            elif event_type == "response.completed":
                if _usage_out is not None:
                    usage_data: dict[str, Any] = (event.get("response") or {}).get("usage") or {}
                    _usage_out.append(dict(usage_data))
                break
            elif event_type in ("response.error", "error"):
                err_obj: dict[str, Any] = event.get("error") or {}
                err_msg: str = err_obj.get("message") or event.get("message") or event_type
                raise OpenAiError(f"OpenAI stream error: {err_msg}")


def _to_responses_input_items(
    messages: list[AgentMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Extract system instructions and map remaining ``AgentMessage``s to Responses API input items.

    Returns ``(instructions, input_items)``.  ``instructions`` is the content of the first system
    message (empty string when none).  ``input_items`` is the ordered list of Responses API input
    objects:
    - ``{"role": "user"|"assistant", "content": "..."}`` for plain text turns
    - ``{"type": "function_call", ...}`` for assistant tool-call requests
    - ``{"type": "function_call_output", ...}`` for tool results
    """
    instructions = ""
    items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            instructions = msg.content
        elif msg.role == "user":
            items.append({"role": "user", "content": msg.content})
        elif msg.role == "assistant":
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.id,
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        }
                    )
                if msg.content:
                    items.append({"role": "assistant", "content": msg.content})
            else:
                items.append({"role": "assistant", "content": msg.content})
        elif msg.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": msg.content,
                }
            )
    return instructions, items


def openai_stream_responses(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: float = 120.0,
    reasoning_effort: str | None = None,
    _usage_out: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[str, str]]:
    """Stream answer and reasoning-summary deltas via the OpenAI Responses API (POST /responses).

    Yields ``(kind, text)`` tuples where ``kind`` is ``"reasoning"`` or ``"answer"``.  Reasoning
    summary deltas are only present for reasoning models when ``reasoning_effort`` is provided;
    the API emits ``response.reasoning_summary_text.delta`` events with ``summary: "auto"``.

    On ``response.completed`` the raw ``usage`` dict is appended to ``_usage_out`` (when provided)
    so the caller can update token accounting without coupling the generator's return value.

    The process-wide concurrency semaphore is held for the full duration of the stream so the
    total in-flight request count (streaming + non-streaming) stays within
    ``DOKTOK_OPENAI_MAX_CONCURRENCY``.
    """
    url = f"{base_url.rstrip('/')}/responses"
    payload: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "input": user,
        "stream": True,
    }
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    yield from _execute_responses_stream(
        url=url, headers=headers, payload=payload, timeout=timeout, _usage_out=_usage_out
    )


def openai_stream_responses_messages(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[AgentMessage],
    timeout: float = 120.0,
    reasoning_effort: str | None = None,
    _usage_out: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[str, str]]:
    """Stream via the Responses API using a full ``AgentMessage`` list as context.

    Extracts the system message as ``instructions``; maps the remaining messages to Responses API
    input items (user/assistant text turns, ``function_call`` items for tool requests, and
    ``function_call_output`` items for tool results).  The model generates its final answer with
    the full conversation context visible.

    Yields ``(kind, text)`` and populates ``_usage_out`` identically to
    ``openai_stream_responses``.  The process-wide concurrency semaphore is held for the full
    duration of the stream.
    """
    instructions, input_items = _to_responses_input_items(messages)
    url = f"{base_url.rstrip('/')}/responses"
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "stream": True,
    }
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort, "summary": "auto"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    yield from _execute_responses_stream(
        url=url, headers=headers, payload=payload, timeout=timeout, _usage_out=_usage_out
    )


def loads_object(content: str) -> dict[str, Any] | None:
    content = content.strip()
    # Tolerate a ```json fence if the model adds one.
    if content.startswith("```"):
        content = content.strip("`")
        content = content[4:] if content.lower().startswith("json") else content
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def repair_json(
    *,
    api_key: str,
    base_url: str,
    model: str,
    broken: str,
    shape_hint: str,
    timeout: float = 120.0,
    reasoning_effort: str | None = None,
) -> str:
    """Re-ask the model to fix malformed/truncated JSON; return the corrected raw content.

    Non-strict ``json_schema`` mode still emits malformed or truncated JSON on dense documents, and
    a single bad generation otherwise fails a whole enrichment feature. This second pass mirrors the
    Ollama adapter's repair fallback: free-form (no schema), asking for ONLY corrected JSON of the
    expected ``shape_hint`` (e.g. ``'{"transactions": [...]}'``) with incomplete trailing entries
    dropped. Callers re-parse the result and raise only if it is still unparseable.
    """
    prompt = (
        f"The text below should be JSON like {shape_hint} but may be malformed or truncated. "
        "Return ONLY corrected JSON, dropping any incomplete trailing entry.\n\nText:\n" + broken
    )
    return openai_chat(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="Output only valid JSON.",
        user=prompt,
        timeout=timeout,
        reasoning_effort=reasoning_effort,
    )
