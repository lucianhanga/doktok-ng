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
from typing import Any

import httpx
from doktok_contracts.media import LlmUsage

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
