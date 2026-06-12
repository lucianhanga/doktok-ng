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
            model="qwen3:14b",
            label="Qwen3 14B - dense, local (fast)",
            contexts=[4096, 8192, 16384],
            supports_reasoning=True,
        ),
        ModelOption(
            provider="ollama",
            model="qwen3.6:35b-a3b",
            label="Qwen3.6 35B-A3B - MoE, local (higher quality)",
            contexts=[8192, 16384, 32768],
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
            model="qwen3:14b",
            label="Qwen3 14B - dense, local (lighter)",
            contexts=[8192, 16384, 32768],
            supports_reasoning=True,
        ),
    ],
    reasoning_levels=REASONING_LEVELS,
)


def ollama_think_for(reasoning: str, model: str, *, structured: bool) -> bool:
    """Map a reasoning-density level to Ollama's binary ``think`` for a given model.

    On the qwen3.6 MoE arch, ``think=false`` + structured ``format`` is broken, so thinking must
    stay on whenever structured output is requested - regardless of the chosen density.
    """
    if structured and "a3b" in model:
        return True
    return reasoning != "off"
