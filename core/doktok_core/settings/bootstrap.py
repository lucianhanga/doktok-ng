"""Headless bootstrap of the AI provider split (APP-2).

A fresh deployment (e.g. the hybrid N95 setup, ADR-0020) needs the pipeline/RAG provider selection
in ``app_settings`` without anyone opening the Settings UI. When ``DOKTOK_PIPELINE_PROVIDER`` /
``DOKTOK_RAG_PROVIDER`` are set and no AI settings have been saved yet, seed them on startup.

Seed-if-absent: it never overwrites settings an operator has already saved (``has_ai_settings``), so
it is safe to call on every start. The OpenAI key itself is resolved separately (APP-7).
"""

from __future__ import annotations

import logging

from doktok_contracts.ports import AppSettingsRepository
from doktok_contracts.schemas import AiPurposeSettings, AiSettings

from doktok_core.config import Settings
from doktok_core.settings.catalog import MODEL_CATALOG

logger = logging.getLogger("doktok.settings.bootstrap")


def _resolve_purpose(
    purpose: str, provider: str, model: str, default: AiPurposeSettings
) -> AiPurposeSettings:
    """Build the purpose settings from env, filling model/context from the catalog when omitted."""
    options = MODEL_CATALOG.pipeline if purpose == "pipeline" else MODEL_CATALOG.rag
    candidates = [o for o in options if o.provider == provider]
    match = (
        next((o for o in candidates if o.model == model), None)
        if model
        else (candidates[0] if candidates else None)
    )
    chosen_model = model or (match.model if match else default.model)
    num_ctx = match.contexts[0] if (match and match.contexts) else default.num_ctx
    return AiPurposeSettings(
        provider=provider, model=chosen_model, num_ctx=num_ctx, reasoning=default.reasoning
    )


def seed_ai_settings(repo: AppSettingsRepository, settings: Settings) -> bool:
    """Seed the AI provider split from env if none is saved yet. Returns True if it wrote."""
    if not (settings.pipeline_provider or settings.rag_provider):
        return False
    if repo.has_ai_settings():
        return False

    current = repo.get_ai_settings()  # the unset defaults
    pipeline = (
        _resolve_purpose(
            "pipeline", settings.pipeline_provider, settings.pipeline_model, current.pipeline
        )
        if settings.pipeline_provider
        else current.pipeline
    )
    rag = (
        _resolve_purpose("rag", settings.rag_provider, settings.rag_model, current.rag)
        if settings.rag_provider
        else current.rag
    )
    repo.set_ai_settings(AiSettings(pipeline=pipeline, rag=rag))
    logger.info(
        "seeded AI settings from env: pipeline=%s/%s rag=%s/%s",
        pipeline.provider,
        pipeline.model,
        rag.provider,
        rag.model,
    )
    return True
