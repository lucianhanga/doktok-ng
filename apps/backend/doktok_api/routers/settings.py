"""System settings (the Settings tab). Global config; token-protected.

These are single-user system settings (not tenant-scoped) - any authenticated caller reads/writes
them. Changes are persisted and take effect on the next worker/backend restart. The OpenAI key is
write-only: it is never returned, only set/cleared, and GET reports only whether one is configured.
"""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import AppSettingsRepository, ChatModelProvider, RagAnswerer
from doktok_contracts.schemas import (
    AiSettings,
    AiSettingsResponse,
    AiSettingsUpdate,
    ModelCatalog,
    OcrSettings,
)
from doktok_core.settings.catalog import MODEL_CATALOG
from fastapi import APIRouter, Depends, Request

from doktok_api.dependencies import Tenant, get_app_settings_repository

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

Repo = Annotated[AppSettingsRepository, Depends(get_app_settings_repository)]


def _response(repo: AppSettingsRepository, ai: AiSettings, request: Request) -> AiSettingsResponse:
    settings = request.app.state.settings
    return AiSettingsResponse(
        **ai.model_dump(),
        openai_api_key_set=bool(repo.get_openai_api_key()),
        embedding_model=settings.embedding_model,
        embedding_num_ctx=settings.embedding_num_ctx,
    )


@router.get("/ai/catalog", response_model=ModelCatalog)
def ai_model_catalog(tenant: Tenant) -> ModelCatalog:
    """The selectable models per AI purpose + the reasoning-density levels."""
    _ = tenant  # auth-gated; the catalog is the same for everyone
    return MODEL_CATALOG


@router.get("/ai", response_model=AiSettingsResponse)
def get_ai_settings(request: Request, tenant: Tenant, repo: Repo) -> AiSettingsResponse:
    """Current AI model selection (the OpenAI key is never returned, only whether it is set)."""
    _ = tenant
    return _response(repo, repo.get_ai_settings(), request)


@router.put("/ai", response_model=AiSettingsResponse)
def put_ai_settings(
    update: AiSettingsUpdate, request: Request, tenant: Tenant, repo: Repo
) -> AiSettingsResponse:
    """Persist the AI model selection and apply it immediately for the RAG/chat path.

    The backend caches the chat model + answerer in the registry; dropping those bindings makes the
    next chat request rebuild them with the new selection - no backend restart. (Worker-side
    pipeline extraction still picks up the new model on its next reconcile/restart.)
    """
    _ = tenant
    ai = AiSettings(pipeline=update.pipeline, rag=update.rag)
    repo.set_ai_settings(ai)
    if update.openai_api_key is not None:  # None leaves it unchanged; "" clears it
        repo.set_openai_api_key(update.openai_api_key)
    registry = request.app.state.registry
    registry.unregister(ChatModelProvider)
    registry.unregister(RagAnswerer)
    return _response(repo, ai, request)


@router.get("/ocr", response_model=OcrSettings)
def get_ocr_settings(tenant: Tenant, repo: Repo) -> OcrSettings:
    """Current OCR processing settings (parallel OCR processes)."""
    _ = tenant
    return repo.get_ocr_settings()


@router.put("/ocr", response_model=OcrSettings)
def put_ocr_settings(update: OcrSettings, tenant: Tenant, repo: Repo) -> OcrSettings:
    """Persist OCR settings. The worker live-reloads the pool between ingest scans (no restart)."""
    _ = tenant
    repo.set_ocr_settings(update)
    return update
