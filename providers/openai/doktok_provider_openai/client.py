"""Shared OpenAI Chat Completions client (httpx; no SDK dependency).

Reasoning models (gpt-5*, o-series) take ``reasoning_effort`` and reject a custom temperature; other
models take ``temperature``. JSON output is requested with a (non-strict) ``json_schema`` response
format so the lenient core parsers can validate/normalize the result.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


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
