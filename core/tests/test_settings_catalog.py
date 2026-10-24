from doktok_core.settings.catalog import (
    MODEL_CATALOG,
    ollama_think_for,
    openai_reasoning_effort,
)


def test_catalog_offers_ollama_and_openai_per_purpose() -> None:
    providers = {o.provider for o in MODEL_CATALOG.pipeline + MODEL_CATALOG.rag}
    assert providers == {"ollama", "openai"}
    assert MODEL_CATALOG.reasoning_levels == ["off", "low", "medium", "high"]


def test_ner_and_keg_offer_local_span_models_and_openai() -> None:
    # ADR-0023: NER offers local GLiNER + remote OpenAI; KEG offers local GLiNER-Relex + OpenAI.
    assert {o.provider for o in MODEL_CATALOG.ner} == {"gliner", "openai"}
    assert {o.provider for o in MODEL_CATALOG.keg} == {"gliner-relex", "openai"}
    # Local span models are on-host: never flagged as egress; the OpenAI options are.
    options = MODEL_CATALOG.ner + MODEL_CATALOG.keg
    local = [o for o in options if o.provider != "openai"]
    assert local and all(o.requires_egress is False for o in local)
    assert all(o.requires_egress for o in options if o.provider == "openai")


def test_ollama_think_keeps_moe_thinking_on_for_structured() -> None:
    # An MoE arch (an "a3b" model) + structured output: think must stay on even with 'off'.
    assert ollama_think_for("off", "qwen3:30b-a3b", structured=True) is True
    # A dense model + structured output honours the density (no forced thinking).
    assert ollama_think_for("off", "qwen3.6:27b", structured=True) is False
    # Non-structured output honours the chosen density (think follows the reasoning level).
    assert ollama_think_for("off", "qwen3.6:27b", structured=False) is False
    assert ollama_think_for("high", "qwen3.6:27b", structured=False) is True


def test_openai_reasoning_effort_only_for_reasoning_models() -> None:
    assert openai_reasoning_effort("medium", "gpt-4o-mini") is None  # not a reasoning model
    assert openai_reasoning_effort("off", "gpt-5-mini") == "minimal"
    assert openai_reasoning_effort("high", "gpt-5-nano") == "high"


def test_luna_offered_as_cheap_remote_options() -> None:
    # gpt-6-luna (OpenAI's cheapest latest-tier model) is selectable for the enrichment pipeline
    # and RAG/interrogation; like every OpenAI option it is marked as egressing, and as a gpt-6
    # model it supports the reasoning-density knob.
    for options in (MODEL_CATALOG.pipeline, MODEL_CATALOG.rag):
        luna = [o for o in options if o.model == "gpt-6-luna"]
        assert luna, "gpt-6-luna should be offered for pipeline and rag"
        assert luna[0].provider == "openai"
        assert luna[0].requires_egress is True
        assert luna[0].supports_reasoning is True


def test_openai_reasoning_effort_covers_gpt6_models() -> None:
    assert openai_reasoning_effort("low", "gpt-6-luna") == "low"
    assert openai_reasoning_effort("off", "gpt-6-luna") == "minimal"
