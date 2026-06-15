"""Shared OpenAI Chat Completions client (httpx; no SDK dependency).

Reasoning models (gpt-5*, o-series) take ``reasoning_effort`` and reject a custom temperature; other
models take ``temperature``. JSON output is requested with a (non-strict) ``json_schema`` response
format so the lenient core parsers can validate/normalize the result.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from doktok_contracts.media import LlmUsage


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
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
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
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
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
