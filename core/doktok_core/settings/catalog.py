"""The models the Settings UI offers per AI purpose, and the reasoning-density mapping.

Single source of truth for what the AI section can select. Ollama models are local; OpenAI options
arrive with the OpenAI provider. Reasoning density is a unified 'off|low|medium|high' that each
provider maps to its own knob (Ollama think on/off; OpenAI reasoning_effort).
"""

from __future__ import annotations

from doktok_contracts.schemas import ModelCatalog, ModelOption

REASONING_LEVELS = ["off", "low", "medium", "high"]

MODEL_CATALOG = ModelCatalog(
    pipeline=[
        ModelOption(
            provider="ollama",
            model="qwen3.6:35b-a3b",
            label="Qwen3.6 35B-A3B - MoE, local",
            contexts=[8192, 16384, 32768],
            supports_reasoning=True,
        ),
        ModelOption(
            provider="ollama",
            model="qwen3.6:27b",
            label="Qwen3.6 27B - dense, local",
            contexts=[8192, 16384, 32768],
            supports_reasoning=True,
        ),
        ModelOption(
            provider="openai",
            model="gpt-4o-mini",
            label="OpenAI gpt-4o-mini - cheap remote (easy job)",
            contexts=[128000],
            supports_reasoning=False,
        ),
        ModelOption(
            provider="openai",
            model="gpt-5-nano",
            label="OpenAI gpt-5-nano - cheapest reasoning remote",
            contexts=[128000],
            supports_reasoning=True,
        ),
    ],
    rag=[
        ModelOption(
            provider="ollama",
            model="qwen3.6:35b-a3b",
            label="Qwen3.6 35B-A3B - MoE, local (recommended)",
            contexts=[8192, 16384, 32768],
            supports_reasoning=True,
        ),
        ModelOption(
            provider="ollama",
            model="qwen3.6:27b",
            label="Qwen3.6 27B - dense, local",
            contexts=[8192, 16384, 32768],
            supports_reasoning=True,
        ),
        ModelOption(
            provider="openai",
            model="gpt-4o-mini",
            label="OpenAI gpt-4o-mini - cheap remote",
            contexts=[128000],
            supports_reasoning=False,
        ),
        ModelOption(
            provider="openai",
            model="gpt-5-mini",
            label="OpenAI gpt-5-mini - cheap reasoning remote",
            contexts=[128000],
            supports_reasoning=True,
        ),
    ],
    # Entity NER (M7.4 / ADR-0023): a local span model or a remote LLM. The local GLiNER option runs
    # on-host (no egress); contexts/reasoning are ignored by the span model (placeholder ctx).
    ner=[
        ModelOption(
            provider="openai",
            model="gpt-4o-mini",
            label="OpenAI gpt-4o-mini - remote (most accurate)",
            contexts=[128000],
            supports_reasoning=False,
        ),
        ModelOption(
            provider="gliner",
            model="gliner-community/gliner_large-v2.5",
            label="GLiNER - local span model (fast, no egress)",
            contexts=[8192],
            supports_reasoning=False,
        ),
    ],
    # KAG relation extraction (ADR-0023): local GLiNER-Relex (benchmark winner) or a remote LLM.
    keg=[
        ModelOption(
            provider="gliner-relex",
            model="knowledgator/gliner-relex-large-v1.0",
            label="GLiNER-Relex - local (best F1, no egress)",
            contexts=[8192],
            supports_reasoning=False,
        ),
        ModelOption(
            provider="openai",
            model="gpt-4o-mini",
            label="OpenAI gpt-4o-mini - remote",
            contexts=[128000],
            supports_reasoning=False,
        ),
    ],
    reasoning_levels=REASONING_LEVELS,
)

# Every remote (OpenAI) option moves content off-host; mark it once here so there is a single rule
# (no per-literal drift). A remote Ollama *URL* is gated separately, per-purpose, at request time.
# Local span models (gliner / gliner-relex) are on-host and stay requires_egress=False.
_ALL_OPTIONS = (*MODEL_CATALOG.pipeline, *MODEL_CATALOG.ner, *MODEL_CATALOG.keg, *MODEL_CATALOG.rag)
for _option in _ALL_OPTIONS:
    _option.requires_egress = _option.provider == "openai"

# OpenAI models whose name starts with one of these reason; they take reasoning_effort, not temp.
_OPENAI_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def ollama_think_for(reasoning: str, model: str, *, structured: bool) -> bool:
    """Map a reasoning-density level to Ollama's binary ``think`` for a given model.

    On the qwen3.6 MoE arch, ``think=false`` + structured ``format`` is broken, so thinking must
    stay on whenever structured output is requested - regardless of the chosen density.
    """
    if structured and "a3b" in model:
        return True
    return reasoning != "off"


def openai_reasoning_effort(reasoning: str, model: str) -> str | None:
    """Map a density level to OpenAI ``reasoning_effort`` (None for non-reasoning models)."""
    if not model.startswith(_OPENAI_REASONING_PREFIXES):
        return None
    return "minimal" if reasoning == "off" else reasoning
