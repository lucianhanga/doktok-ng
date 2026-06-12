"""System settings (the Settings tab). Global config; token-protected.

These are single-user system settings (not tenant-scoped) - any authenticated caller reads/writes
them. Changes are persisted and take effect on the next worker/backend restart. The OpenAI key is
write-only: it is never returned, only set/cleared, and GET reports only whether one is configured.
"""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import AppSettingsRepository
from doktok_contracts.schemas import (
    AiSettings,
    AiSettingsResponse,
    AiSettingsUpdate,
    ModelCatalog,
)
from doktok_core.settings.catalog import MODEL_CATALOG
from fastapi import APIRouter, Depends

from doktok_api.dependencies import Tenant, get_app_settings_repository

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

Repo = Annotated[AppSettingsRepository, Depends(get_app_settings_repository)]


def _response(repo: AppSettingsRepository, ai: AiSettings) -> AiSettingsResponse:
    return AiSettingsResponse(**ai.model_dump(), openai_api_key_set=bool(repo.get_openai_api_key()))


@router.get("/ai/catalog", response_model=ModelCatalog)
def ai_model_catalog(tenant: Tenant) -> ModelCatalog:
    """The selectable models per AI purpose + the reasoning-density levels."""
    _ = tenant  # auth-gated; the catalog is the same for everyone
    return MODEL_CATALOG


@router.get("/ai", response_model=AiSettingsResponse)
def get_ai_settings(tenant: Tenant, repo: Repo) -> AiSettingsResponse:
    """Current AI model selection (the OpenAI key is never returned, only whether it is set)."""
    _ = tenant
    return _response(repo, repo.get_ai_settings())


@router.put("/ai", response_model=AiSettingsResponse)
def put_ai_settings(update: AiSettingsUpdate, tenant: Tenant, repo: Repo) -> AiSettingsResponse:
    """Persist the AI model selection. Takes effect on the next restart."""
    _ = tenant
    ai = AiSettings(pipeline=update.pipeline, rag=update.rag)
    repo.set_ai_settings(ai)
    if update.openai_api_key is not None:  # None leaves it unchanged; "" clears it
        repo.set_openai_api_key(update.openai_api_key)
    return _response(repo, ai)
