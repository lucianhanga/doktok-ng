"""Shared token-usage parsing for the Ollama enrichment/embedding providers.

The enrichment extractors (metadata/classify/ner/records) and the embedding client all read the
``prompt_eval_count``/``eval_count`` already present in the Ollama response body and expose the
result via ``get_last_usage()`` (mirroring ``OllamaChatModelProvider`` / the
``UsageReportingChatModel`` protocol). The reconciler picks this up through the feature processor
and persists it as per-step telemetry. Best-effort: missing counts -> 0; a char-ratio estimate
sets ``estimated=True``.
"""

from __future__ import annotations

from typing import Any

from doktok_contracts.media import LlmUsage


def _as_int(value: object) -> int:
    """Coerce an Ollama JSON field (typed ``object``) to int; 0 when absent or non-numeric."""
    return value if isinstance(value, int) else 0


def _est_tokens(chars: int) -> int:
    """Character-based token estimate (~3.5 chars/token) when no provider counter is available."""
    return max(0, round(chars / 3.5))


def usage_from_chat(body: dict[str, Any], answer_text: str) -> LlmUsage:
    """Build LlmUsage from an Ollama ``/api/chat`` (non-streaming) response body. ``answer_text`` is
    used only to estimate answer tokens when the provider omits ``eval_count`` (then estimated)."""
    eval_count = body.get("eval_count")
    eval_ns = body.get("eval_duration")
    return LlmUsage(
        prompt_tokens=_as_int(body.get("prompt_eval_count")),
        answer_tokens=_as_int(eval_count)
        if eval_count is not None
        else _est_tokens(len(answer_text)),
        reasoning_tokens=0,
        eval_ms=round(_as_int(eval_ns) / 1_000_000) if eval_ns else None,
        estimated=eval_count is None,
    )


def usage_from_embed(body: dict[str, Any]) -> LlmUsage:
    """Build LlmUsage from an Ollama ``/api/embed`` response body. Embedding is input-only, so the
    prompt-token count is the meaningful figure (answer/reasoning are 0)."""
    prompt = body.get("prompt_eval_count")
    return LlmUsage(
        prompt_tokens=_as_int(prompt),
        answer_tokens=0,
        reasoning_tokens=0,
        estimated=prompt is None,
    )
