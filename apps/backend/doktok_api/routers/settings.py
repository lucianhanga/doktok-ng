"""System settings (the Settings tab). Global config; token-protected.

These are single-user system settings (not tenant-scoped) - any authenticated caller reads/writes
them. Changes are persisted and take effect on the next worker/backend restart. The OpenAI key is
write-only: it is never returned, only set/cleared, and GET reports only whether one is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from doktok_contracts.ports import AppSettingsRepository, ChatModelProvider, RagAnswerer
from doktok_contracts.schemas import (
    AiSettings,
    AiSettingsResponse,
    AiSettingsUpdate,
    BackupLegStatus,
    DrpConfig,
    DrpStatus,
    DrpStatusResponse,
    ModelCatalog,
    OcrSettings,
)
from doktok_core.security.egress import openai_egress_allowed
from doktok_core.settings.catalog import MODEL_CATALOG
from fastapi import APIRouter, Depends, Request

from doktok_api.dependencies import Tenant, get_app_settings_repository

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

Repo = Annotated[AppSettingsRepository, Depends(get_app_settings_repository)]


def _response(repo: AppSettingsRepository, ai: AiSettings, request: Request) -> AiSettingsResponse:
    settings = request.app.state.settings
    key = repo.get_openai_api_key() or settings.openai_api_key
    remote_selected = "openai" in (ai.pipeline.provider, ai.rag.provider)
    return AiSettingsResponse(
        **ai.model_dump(),
        openai_api_key_set=bool(repo.get_openai_api_key()),
        embedding_model=settings.embedding_model,
        embedding_num_ctx=settings.embedding_num_ctx,
        egress_active=remote_selected
        and openai_egress_allowed(key=key, no_egress=settings.no_egress),
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


def _leg_status(raw: dict[str, object] | None, rpo_seconds: int, now: datetime) -> BackupLegStatus:
    """Derive a read-only leg status from a sentinel dict (DRP, #368). Missing -> unknown (neutral);
    ok:false -> failed; age > 3x RPO -> stale; else ok. Mirrors the worker-heartbeat tolerance."""
    if not raw or not raw.get("last_run_at"):
        return BackupLegStatus(state="unknown", detail=str(raw.get("detail", "")) if raw else "")
    try:
        ts = datetime.fromisoformat(str(raw["last_run_at"]).replace("Z", "+00:00"))
    except ValueError:
        return BackupLegStatus(state="unknown")
    age = int((now - ts).total_seconds())
    detail = str(raw.get("detail", ""))
    if raw.get("ok") is False:
        state = "failed"
    elif age > 3 * rpo_seconds:
        state = "stale"
    else:
        state = "ok"
    return BackupLegStatus(state=state, last_run_at=ts, age_seconds=age, detail=detail)


@router.get("/drp", response_model=DrpStatusResponse)
def get_drp(request: Request, tenant: Tenant, repo: Repo) -> DrpStatusResponse:
    """Read-only Disaster Recovery Plan status + config (#368): backup freshness from the host
    sentinels (outside the DB) plus static targets/config. Never returns a secret value."""
    _ = tenant
    settings = request.app.state.settings
    raw = repo.get_backup_status()
    cfg = DrpConfig()  # defaults carry the RPO/RTO targets
    now = datetime.now(UTC)
    status = DrpStatus(status_source_available=raw is not None)
    if raw is not None:
        status.files = _leg_status(raw.get("files"), cfg.rpo_files_seconds, now)
        status.pg = _leg_status(raw.get("pg"), cfg.rpo_pg_seconds, now)
        status.offsite = _leg_status(raw.get("offsite"), cfg.rpo_offsite_seconds, now)
        status.drill = _leg_status(raw.get("drill"), 3_024_000, now)  # ~35 days
        wal = (raw.get("pg") or {}).get("wal_lag_s")
        status.wal_lag_seconds = int(wal) if isinstance(wal, int | float) else None
    config = DrpConfig(
        repo_location=settings.backup_dir,
        azure_container=settings.azure_container,
        immutability_enabled=settings.azure_immutable,
        encryption_keys_configured=bool(settings.restic_password)
        and bool(settings.pgbackrest_cipher_pass),
        azure_credentials_configured=bool(settings.azure_sas),
    )
    return DrpStatusResponse(status=status, config=config)
