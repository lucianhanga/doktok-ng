"""System settings (the Settings tab). Global config; token-protected.

These are single-user system settings (not tenant-scoped) - any authenticated caller reads/writes
them. Changes are persisted and take effect on the next worker/backend restart. The OpenAI key is
write-only: it is never returned, only set/cleared, and GET reports only whether one is configured.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    ChatModelProvider,
    RagAnswerer,
)
from doktok_contracts.schemas import (
    OCR_ENGINES,
    AiSettings,
    AiSettingsResponse,
    AiSettingsUpdate,
    AuditEventType,
    BackupEvent,
    BackupExportInfo,
    BackupLegStatus,
    DrillTriggerResponse,
    DrpConfig,
    DrpHistoryResponse,
    DrpStatus,
    DrpStatusResponse,
    EgressBlockReason,
    ModelCatalog,
    OcrRecommendation,
    OcrSettings,
    OllamaStatus,
    OllamaTestRequest,
    OllamaTestResult,
    OllamaWarmupRequest,
    OllamaWarmupResult,
    OpenAiTestRequest,
    OpenAiTestResult,
    PurposeEgressStatus,
    RestoreApplyRequest,
    RestorePreview,
    RestoreResult,
    RestoreStatus,
)
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.backup.export import ExportPaths
from doktok_core.security.egress import effective_no_egress, purpose_requires_egress
from doktok_core.settings.catalog import MODEL_CATALOG
from doktok_core.settings.ocr_recommend import recommend_ocr
from doktok_core.settings.runtime import local_ollama_needed
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic import Field as PydField

from doktok_api.dependencies import (
    Tenant,
    get_app_settings_repository,
    get_audit_repository,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

Repo = Annotated[AppSettingsRepository, Depends(get_app_settings_repository)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]


def _purpose_status(
    provider: str,
    ollama_base_url: str | None,
    *,
    no_egress: bool,
    default_url: str,
    key_set: bool,
) -> PurposeEgressStatus:
    """Resolve one purpose's runtime egress status against the active posture (used by GET and the
    egress_active indicator). Policy blocks (egress refused) are distinct from a missing key."""
    requires = purpose_requires_egress(provider, ollama_base_url, default_url=default_url)
    if no_egress and requires:
        reason = (
            EgressBlockReason.OPENAI_SELECTED
            if provider == "openai"
            else EgressBlockReason.REMOTE_OLLAMA_URL
        )
        return PurposeEgressStatus(requires_egress=True, usable=False, blocked_reason=reason)
    if provider == "openai" and not key_set:
        return PurposeEgressStatus(
            requires_egress=True, usable=False, blocked_reason=EgressBlockReason.OPENAI_KEY_MISSING
        )
    return PurposeEgressStatus(requires_egress=requires, usable=True)


def _purpose_statuses(
    ai: AiSettings, *, no_egress: bool, default_url: str, key_set: bool
) -> dict[str, PurposeEgressStatus]:
    def status(provider: str, url: str | None) -> PurposeEgressStatus:
        return _purpose_status(
            provider, url, no_egress=no_egress, default_url=default_url, key_set=key_set
        )

    return {
        "pipeline": status(ai.pipeline.provider, ai.pipeline.ollama_base_url),
        "ner": status(ai.ner.provider, ai.ner.ollama_base_url),
        "keg": status(ai.keg.provider, ai.keg.ollama_base_url),
        "rerank": status(ai.rerank.provider, ai.rerank.ollama_base_url),
        "rag": status(ai.rag.provider, ai.rag.ollama_base_url),
        # Embedding has no provider switch - only the URL vector can egress.
        "embedding": status("ollama", ai.embedding.ollama_base_url),
    }


def _egress_violations(
    ai: AiSettings, *, no_egress: bool, default_url: str
) -> list[dict[str, str]]:
    """Per-field violations when a config would egress under no-egress (drives the PUT 422)."""
    if not no_egress:
        return []
    purposes = (
        ("pipeline", ai.pipeline.provider, ai.pipeline.ollama_base_url),
        ("ner", ai.ner.provider, ai.ner.ollama_base_url),
        ("keg", ai.keg.provider, ai.keg.ollama_base_url),
        ("rerank", ai.rerank.provider, ai.rerank.ollama_base_url),
        ("rag", ai.rag.provider, ai.rag.ollama_base_url),
        ("embedding", "ollama", ai.embedding.ollama_base_url),
    )
    violations: list[dict[str, str]] = []
    for purpose, provider, url in purposes:
        if purpose_requires_egress(provider, url, default_url=default_url):
            if provider == "openai":
                violations.append(
                    {
                        "purpose": purpose,
                        "reason": EgressBlockReason.OPENAI_SELECTED.value,
                        "value": provider,
                    }
                )
            else:
                violations.append(
                    {
                        "purpose": purpose,
                        "reason": EgressBlockReason.REMOTE_OLLAMA_URL.value,
                        "value": url or default_url,
                    }
                )
    return violations


def _resolve_no_egress(repo: AppSettingsRepository, settings: Any) -> tuple[bool, bool]:
    """The effective no-egress posture + whether the host has hard-locked it (toggle disabled)."""
    locked = bool(settings.no_egress_lock)
    no_egress = effective_no_egress(
        repo.get_no_egress(), env_default=settings.no_egress, lock=locked
    )
    return no_egress, locked


def _response(repo: AppSettingsRepository, ai: AiSettings, request: Request) -> AiSettingsResponse:
    settings = request.app.state.settings
    no_egress, locked = _resolve_no_egress(repo, settings)
    key_set = bool(repo.get_openai_api_key() or settings.openai_api_key)
    status = _purpose_statuses(
        ai, no_egress=no_egress, default_url=settings.ollama_base_url, key_set=key_set
    )
    return AiSettingsResponse(
        **ai.model_dump(),
        openai_api_key_set=bool(repo.get_openai_api_key()),
        embedding_model=settings.embedding_model,
        embedding_num_ctx=settings.embedding_num_ctx,
        ollama_base_url_default=settings.ollama_base_url,
        no_egress=no_egress,
        no_egress_locked=locked,
        purpose_status=status,
        egress_active=any(s.requires_egress and s.usable for s in status.values()),
    )


def _validate_ollama_url(value: str | None, field: str) -> None:
    """Per-purpose Ollama URL overrides must be a well-formed http(s) URL (M13 #369). Empty/None is
    fine (it means "inherit the default")."""
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=422, detail=f"{field} must be an http(s) URL, got {value!r}"
        )


@router.get("/ai/catalog", response_model=ModelCatalog)
def ai_model_catalog(request: Request, tenant: Tenant, repo: Repo) -> ModelCatalog:
    """The selectable models per AI purpose + the reasoning-density levels, plus the active
    no-egress posture so the UI can disable/badge the egress-requiring options."""
    _ = tenant  # auth-gated; the catalog is the same for everyone
    no_egress, _locked = _resolve_no_egress(repo, request.app.state.settings)
    return MODEL_CATALOG.model_copy(update={"no_egress": no_egress})


@router.get("/ai", response_model=AiSettingsResponse)
def get_ai_settings(request: Request, tenant: Tenant, repo: Repo) -> AiSettingsResponse:
    """Current AI model selection (the OpenAI key is never returned, only whether it is set)."""
    _ = tenant
    return _response(repo, repo.get_ai_settings(), request)


@router.get("/ollama-status", response_model=OllamaStatus)
def ollama_status(request: Request, tenant: Tenant, repo: Repo) -> OllamaStatus:
    """Whether the in-stack Ollama container is needed (M16 #374). A host timer reads this to stop
    the container when every Ollama consumer is offloaded, and start it again when one is local."""
    _ = tenant
    settings = request.app.state.settings
    ai = repo.get_ai_settings()
    return OllamaStatus(
        local_ollama_needed=local_ollama_needed(
            ai, default_url=settings.ollama_base_url, ocr_engine=settings.ocr_engine
        ),
        embedding_url=ai.embedding.ollama_base_url or settings.ollama_base_url,
    )


@router.put("/ai", response_model=AiSettingsResponse)
def put_ai_settings(
    update: AiSettingsUpdate, request: Request, tenant: Tenant, repo: Repo, audit: Audit
) -> AiSettingsResponse:
    """Persist the AI model selection and apply it immediately for the RAG/chat path.

    The backend caches the chat model + answerer in the registry; dropping those bindings makes the
    next chat request rebuild them with the new selection - no backend restart. (Worker-side
    pipeline extraction still picks up the new model on its next reconcile/restart.)
    """
    _validate_ollama_url(update.pipeline.ollama_base_url, "pipeline.ollama_base_url")
    _validate_ollama_url(update.ner.ollama_base_url, "ner.ollama_base_url")
    _validate_ollama_url(update.keg.ollama_base_url, "keg.ollama_base_url")
    _validate_ollama_url(update.rerank.ollama_base_url, "rerank.ollama_base_url")
    _validate_ollama_url(update.rag.ollama_base_url, "rag.ollama_base_url")
    _validate_ollama_url(update.embedding.ollama_base_url, "embedding.ollama_base_url")
    settings = request.app.state.settings
    prior_no_egress, locked = _resolve_no_egress(repo, settings)
    # The in-app no-egress toggle: None leaves it unchanged. A host lock forbids turning it off.
    if update.no_egress is False and locked:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "no_egress_locked",
                "message": (
                    "No-egress is enforced by the host (DOKTOK_NO_EGRESS_LOCK) and cannot be "
                    "turned off here."
                ),
            },
        )
    new_no_egress = prior_no_egress if (update.no_egress is None or locked) else update.no_egress
    ai = AiSettings(
        pipeline=update.pipeline,
        ner=update.ner,
        keg=update.keg,
        rerank=update.rerank,
        rag=update.rag,
        embedding=update.embedding,
    )
    # Boundary gate: refuse a selection that would send content off-host while no-egress is on,
    # evaluated against the posture THIS save applies. The sinks re-check (defense-in-depth).
    violations = _egress_violations(
        ai, no_egress=new_no_egress, default_url=settings.ollama_base_url
    )
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "egress_not_permitted",
                "message": (
                    "No-egress is on; these selections would send document content off-host. "
                    "Turn off no-egress in Settings > AI to allow remote models."
                ),
                "violations": violations,
            },
        )
    # Capture the prior egress posture so we can audit a false->true transition (the opt-in).
    prior_key_set = bool(repo.get_openai_api_key() or settings.openai_api_key)
    prior_active = any(
        s.requires_egress and s.usable
        for s in _purpose_statuses(
            repo.get_ai_settings(),
            no_egress=prior_no_egress,
            default_url=settings.ollama_base_url,
            key_set=prior_key_set,
        ).values()
    )
    if update.no_egress is not None and not locked:
        repo.set_no_egress(update.no_egress)
    repo.set_ai_settings(ai)
    new_key = update.openai_api_key  # None leaves it unchanged; "" clears it
    key_changed = new_key is not None
    if new_key is not None:
        repo.set_openai_api_key(new_key)
    registry = request.app.state.registry
    registry.unregister(ChatModelProvider)
    registry.unregister(RagAnswerer)
    # Activity log (M15 #373): a non-secret summary of the change - never the key itself.
    summary = (
        f"AI settings: pipeline {ai.pipeline.provider}/{ai.pipeline.model}, "
        f"NER {ai.ner.provider}/{ai.ner.model}, KEG {ai.keg.provider}/{ai.keg.model}, "
        f"reranker {ai.rerank.provider}/{ai.rerank.model}, RAG {ai.rag.provider}/{ai.rag.model}"
    )
    if key_changed:
        summary += ", OpenAI key updated"
    if update.no_egress is not None and not locked and update.no_egress != prior_no_egress:
        summary += f", no-egress turned {'on' if update.no_egress else 'off'}"
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.SETTINGS_CHANGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=summary,
        details={"setting": "ai"},
    )
    response = _response(repo, ai, request)
    # Audit the security-significant opt-in: egress went off->on (content now leaves the host).
    if response.egress_active and not prior_active:
        leaving = [p for p, s in response.purpose_status.items() if s.requires_egress and s.usable]
        record_activity(
            audit,
            tenant.tenant_id,
            AuditEventType.EGRESS_ENABLED,
            actor=actor_identity(tenant),
            actor_kind="user",
            severity="warning",
            description=f"Remote egress enabled for: {', '.join(sorted(leaving))}",
            details={"purposes": sorted(leaving)},
        )
    return response


def _probe_ollama(url: str) -> tuple[bool, str, list[str]]:
    """Ping an Ollama server's /api/tags (M13 #369). Returns (ok, short detail, installed model
    names); never raises. Connection/timeout failures get an actionable hint instead of a raw errno
    so the UI tells "unreachable" from "model missing". /api/tags does NOT load a model."""
    import httpx

    base = url.rstrip("/")
    try:
        resp = httpx.get(f"{base}/api/tags", timeout=5.0)
    except httpx.ConnectError:
        return (False, "could not connect - is Ollama running and bound to 0.0.0.0:11434 here?", [])
    except httpx.TimeoutException:
        return (False, "timed out - the server did not respond within 5s", [])
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return (False, str(exc).splitlines()[0][:200] or "connection failed", [])
    if resp.status_code >= 400:
        return (False, f"HTTP {resp.status_code}", [])
    names = [str(m.get("name", "")) for m in resp.json().get("models", []) if m.get("name")]
    return (True, f"reachable - {len(names)} model(s) installed", names)


def _model_installed(model: str, names: list[str]) -> bool:
    """Whether ``model`` is among the installed Ollama model names. Exact match wins; a name with no
    explicit ``:tag`` matches any installed tag of that repo (e.g. 'qwen3' -> 'qwen3:latest')."""
    wanted = model.strip()
    if not wanted:
        return False
    if wanted in names:
        return True
    if ":" not in wanted:
        return any(n.split(":")[0] == wanted for n in names)
    return False


def _warmup_ollama(url: str, model: str) -> tuple[bool, str]:
    """Preload a model into Ollama via an empty /api/generate (no prompt -> the model is just loaded
    into memory). Slow on a cold large model; never raises. Returns (ok, short detail)."""
    import httpx

    base = url.rstrip("/")
    try:
        resp = httpx.post(f"{base}/api/generate", json={"model": model}, timeout=180.0)
    except httpx.ConnectError:
        return (False, "could not connect - is Ollama running and reachable here?")
    except httpx.TimeoutException:
        return (False, "timed out loading the model (>180s)")
    except Exception as exc:  # noqa: BLE001 - report failure, never raise
        return (False, str(exc).splitlines()[0][:200] or "warm-up failed")
    if resp.status_code >= 400:
        try:
            msg = str(resp.json().get("error", ""))  # Ollama 404s with a clear "model not found"
        except Exception:  # noqa: BLE001
            msg = ""
        return (False, msg[:200] or f"HTTP {resp.status_code}")
    return (True, f"model '{model}' loaded")


@router.post("/ai/test-ollama", response_model=OllamaTestResult)
def test_ollama_url(req: OllamaTestRequest, request: Request, tenant: Tenant) -> OllamaTestResult:
    """Probe an Ollama server (the override, or the configured default if blank) before saving. When
    a model is supplied, also report whether it is installed (a fast check; no model is loaded)."""
    _ = tenant
    _validate_ollama_url(req.url, "url")
    settings = request.app.state.settings
    url = (req.url or "").strip() or settings.ollama_base_url
    ok, detail, names = _probe_ollama(url)
    model = req.model.strip()
    model_present: bool | None = None
    if ok and model:
        model_present = _model_installed(model, names)
        if model_present:
            detail = f"{detail}; model '{model}' is installed"
        else:
            detail = f"{detail}; model '{model}' is NOT installed (run: ollama pull {model})"
    return OllamaTestResult(ok=ok, detail=detail, url=url, model=model, model_present=model_present)


@router.post("/ai/warmup-ollama", response_model=OllamaWarmupResult)
def warmup_ollama(req: OllamaWarmupRequest, request: Request, tenant: Tenant) -> OllamaWarmupResult:
    """Preload a model into an Ollama server so the first real request is not cold (M13 follow-up).
    Distinct from Test: this deliberately loads the model and can take a while on a large model."""
    _ = tenant
    _validate_ollama_url(req.url, "url")
    settings = request.app.state.settings
    url = (req.url or "").strip() or settings.ollama_base_url
    model = req.model.strip()
    ok, detail = _warmup_ollama(url, model)
    return OllamaWarmupResult(ok=ok, detail=detail, url=url, model=model)


def _probe_openai(key: str) -> tuple[bool, str]:
    """Validate an OpenAI key by listing models (M13). Returns (ok, short detail); never raises and
    never includes the key in the message."""
    import httpx

    try:
        resp = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return (True, f"valid - {len(resp.json().get('data', []))} models available")
        if resp.status_code in (401, 403):
            return (False, f"invalid or unauthorized key (HTTP {resp.status_code})")
        return (False, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return (False, str(exc).splitlines()[0][:200] or "connection failed")


@router.post("/ai/test-openai", response_model=OpenAiTestResult)
def test_openai_key(
    req: OpenAiTestRequest, request: Request, tenant: Tenant, repo: Repo
) -> OpenAiTestResult:
    """Validate the candidate OpenAI key (or the stored one if blank) before saving (M13)."""
    _ = tenant
    settings = request.app.state.settings
    key = (req.api_key or "").strip() or repo.get_openai_api_key() or settings.openai_api_key
    if not key:
        return OpenAiTestResult(ok=False, detail="no API key provided or stored")
    ok, detail = _probe_openai(key)
    return OpenAiTestResult(ok=ok, detail=detail)


def _probe_hardware() -> tuple[str, int, float, bool]:
    """Best-effort host snapshot for the OCR recommendation (M17 #375): CPU vendor, logical cores,
    total RAM (GB), NVIDIA GPU presence. Reads /proc; never raises."""
    import os
    import shutil

    cores = os.cpu_count() or 1
    vendor = ""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("vendor_id"):
                    vendor = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    total_ram_gb = 0.0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_ram_gb = int(line.split()[1]) / (1024 * 1024)
                    break
    except OSError:
        pass
    has_gpu = shutil.which("nvidia-smi") is not None or os.path.exists(
        "/proc/driver/nvidia/version"
    )
    return vendor, cores, total_ram_gb, has_gpu


@router.get("/ocr/recommendation", response_model=OcrRecommendation)
def ocr_recommendation(tenant: Tenant) -> OcrRecommendation:
    """Device-aware OCR suggestion for this host (M17 #375): engine + concurrency + a short why."""
    _ = tenant
    vendor, cores, ram, gpu = _probe_hardware()
    rec = recommend_ocr(cpu_vendor=vendor, logical_cores=cores, total_ram_gb=ram, has_gpu=gpu)
    return OcrRecommendation(engine=rec.engine, concurrency=rec.concurrency, reason=rec.reason)


@router.get("/ocr", response_model=OcrSettings)
def get_ocr_settings(tenant: Tenant, repo: Repo) -> OcrSettings:
    """Current OCR processing settings (parallel OCR processes)."""
    _ = tenant
    return repo.get_ocr_settings()


@router.put("/ocr", response_model=OcrSettings)
def put_ocr_settings(update: OcrSettings, tenant: Tenant, repo: Repo, audit: Audit) -> OcrSettings:
    """Persist OCR settings. Concurrency live-reloads between ingest scans; an engine change applies
    on the next worker restart (M17 #375)."""
    if update.engine and update.engine not in OCR_ENGINES:
        raise HTTPException(
            status_code=422,
            detail=f"engine must be one of {OCR_ENGINES} or empty, got {update.engine!r}",
        )
    repo.set_ocr_settings(update)
    desc = f"OCR parallelism set to {update.ocr_concurrency}"
    if update.engine:
        desc += f", engine {update.engine}"
    record_activity(  # activity log (M15 #373)
        audit,
        tenant.tenant_id,
        AuditEventType.SETTINGS_CHANGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description=desc,
        details={
            "setting": "ocr",
            "ocr_concurrency": update.ocr_concurrency,
            "engine": update.engine,
        },
    )
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
    fc = raw.get("file_count")
    return BackupLegStatus(
        state=state,
        last_run_at=ts,
        age_seconds=age,
        detail=detail,
        size=str(raw.get("size", "")),
        file_count=int(fc) if isinstance(fc, int | float) else None,
        backup_id=str(raw.get("backup_id", "")),
    )


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
        deploy_mode=settings.deploy_mode,
        repo_location=settings.backup_dir,
        azure_container=settings.azure_container,
        immutability_enabled=settings.azure_immutable,
        encryption_keys_configured=bool(settings.restic_password)
        and bool(settings.pgbackrest_cipher_pass),
        azure_credentials_configured=bool(settings.azure_sas),
    )
    return DrpStatusResponse(status=status, config=config)


# Valid history legs (matches the host-side log_event whitelist). A bad leg is a 422, not a silent
# empty result, so a typo in a client surfaces loudly.
_HISTORY_LEGS = ("files", "pg", "offsite", "drill", "prune", "portable", "restore")

# How each history event maps onto an activity-log AuditEventType when we mirror it (M12 DRP
# hardening). Only terminal/meaningful events are mirrored; transient ``start`` events are skipped
# (they are noise in the activity table). Drill outcomes collapse to DRILL_COMPLETED.
_MIRROR_EVENT_TYPES = {
    "success": AuditEventType.BACKUP_COMPLETED,
    "failure": AuditEventType.BACKUP_FAILED,
    "drill_fail": AuditEventType.BACKUP_FAILED,
    "drill_pass": AuditEventType.DRILL_COMPLETED,
    "drill_completed": AuditEventType.DRILL_COMPLETED,
}

_DRILL_COOLDOWN_SECONDS = 600  # 10 min: backend rate-limit for on-demand drills


def _to_backup_event(raw: dict[str, object]) -> BackupEvent | None:
    """Project one raw history dict onto the wire model, whitelisting only the exposed fields (never
    prev_sha256/schema). Returns None when the line is too malformed to render (no leg/event)."""
    leg = raw.get("leg")
    event = raw.get("event")
    ts = raw.get("ts")
    if not isinstance(leg, str) or not isinstance(event, str) or not isinstance(ts, str):
        return None
    try:
        return BackupEvent(
            ts=datetime.fromisoformat(ts.replace("Z", "+00:00")),
            leg=leg,
            event=event,
            ok=bool(raw.get("ok", False)),
            size=str(raw.get("size", "")),
            item_count=_as_int(raw.get("item_count")),
            backup_id=str(raw.get("backup_id", "")),
            duration_ms=_as_int(raw.get("duration_ms")),
            detail=str(raw.get("detail", "")),
            seq=_as_int(raw.get("seq")),
        )
    except (ValueError, TypeError):
        return None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _mirror_history_to_activity(
    audit: AuditLogRepository, tenant_id: str, events: list[BackupEvent]
) -> None:
    """Mirror the returned history window into the activity log, idempotently (M12 DRP hardening).

    Forward-only and cheap: only the events surfaced by THIS read are mirrored (we never replay the
    whole file - that would re-flood the table after a DB restore). Each row gets a DETERMINISTIC id
    derived from (seq, ts, leg, event), so re-reads collapse to one row via the audit repository's
    insert-if-absent (ON CONFLICT DO NOTHING) semantics. The activity rows are explicitly marked
    non-authoritative; the history.jsonl is the source of truth."""
    import hashlib

    for ev in events:
        event_type = _MIRROR_EVENT_TYPES.get(ev.event)
        # The restore leg gets its own dedicated event types (a restore is more significant than a
        # routine backup). success/failure on the restore leg -> RESTORE_COMPLETED/RESTORE_FAILED.
        if ev.leg == "restore" and ev.event in ("success", "failure"):
            event_type = (
                AuditEventType.RESTORE_COMPLETED
                if ev.event == "success"
                else AuditEventType.RESTORE_FAILED
            )
        if event_type is None:
            continue  # skip start/prune-noise; only terminal outcomes are worth an activity row
        seed = f"{ev.seq}|{ev.ts.isoformat()}|{ev.leg}|{ev.event}"
        event_id = "drp-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        desc = f"{ev.leg} backup {ev.event}"
        if ev.event in ("drill_pass", "drill_fail", "drill_completed"):
            desc = f"restore drill {'passed' if ev.ok else 'failed'}"
        elif ev.leg == "restore":
            desc = f"portable restore {'completed' if ev.ok else 'failed'}"
        record_activity(
            audit,
            tenant_id,
            event_type,
            actor="system",
            actor_kind="system",
            description=desc,
            event_id=event_id,
            details={"source": "drp", "authoritative": False},
        )


@router.get("/drp/history", response_model=DrpHistoryResponse)
def get_drp_history(
    request: Request,
    tenant: Tenant,
    repo: Repo,
    audit: Audit,
    limit: int = Query(100, ge=1, le=500),
    leg: str | None = Query(None),
) -> DrpHistoryResponse:
    """Read-only window over the append-only backup history (M12 DRP hardening), newest-first.

    Sourced OUTSIDE Postgres (a host-written ``history.jsonl``) so a DB restore can't roll it back.
    ``integrity_ok`` is False when the prev_sha256 hash chain is broken over the read window, which
    surfaces tampering. Surfaced events are also mirrored into the activity log idempotently."""
    if leg is not None and leg not in _HISTORY_LEGS:
        raise HTTPException(status_code=422, detail=f"leg must be one of {_HISTORY_LEGS}")
    raw_events, source_available, truncated, integrity_ok = repo.get_backup_history(
        limit=limit, leg=leg
    )
    events = [ev for ev in (_to_backup_event(r) for r in raw_events) if ev is not None]
    # Mirror only what this read surfaced (cheap, forward-only, idempotent).
    _mirror_history_to_activity(audit, tenant.tenant_id, events)
    return DrpHistoryResponse(
        events=events,
        source_available=source_available,
        total_returned=len(events),
        truncated=truncated,
        integrity_ok=integrity_ok,
    )


@router.post("/drp/drill", response_model=DrillTriggerResponse)
def trigger_drp_drill(
    request: Request, tenant: Tenant, repo: Repo, audit: Audit
) -> DrillTriggerResponse:
    """Request an on-demand restore drill (M12 DRP hardening). The backend NEVER runs the drill - it
    only drops a request file a root systemd path-unit watches. Rate-limited: 429 if a request is
    already pending OR the last drill completed within the cooldown window."""
    import json as _json
    from pathlib import Path

    settings = request.app.state.settings
    status_dir = Path(f"{settings.backup_dir.rstrip('/')}/status")
    requests_dir = status_dir / "requests"
    request_file = requests_dir / "drill.request"

    # Rate-limit 1: a request is already pending (the host has not consumed it yet).
    last_drill_at = _last_drill_at(repo)
    if request_file.exists():
        raise HTTPException(
            status_code=429,
            detail="a restore drill is already requested and pending",
        )
    # Rate-limit 2: the last drill ran within the cooldown window.
    if last_drill_at is not None:
        age = (datetime.now(UTC) - last_drill_at).total_seconds()
        if age < _DRILL_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"a drill ran {int(age)}s ago; wait {_DRILL_COOLDOWN_SECONDS}s before retry",
            )

    try:
        requests_dir.mkdir(parents=True, exist_ok=True)
        payload = _json.dumps(
            {
                "requested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actor": tenant.tenant_id,
            }
        )
        request_file.write_text(payload, encoding="utf-8")
        request_file.chmod(0o644)
    except OSError as exc:
        # Don't leak the host path/errno detail to the client.
        _ = exc
        raise HTTPException(status_code=503, detail="could not queue the drill request") from None

    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.SETTINGS_CHANGED,
        actor=actor_identity(tenant),
        actor_kind="user",
        description="On-demand restore drill requested",
        details={"setting": "drp", "action": "drill_requested"},
    )
    return DrillTriggerResponse(
        accepted=True, detail="restore drill requested", last_drill_at=last_drill_at
    )


def _last_drill_at(repo: AppSettingsRepository) -> datetime | None:
    """The drill sentinel's last_run_at, if any (for the drill cooldown). None if unavailable."""
    raw = repo.get_backup_status()
    if not raw:
        return None
    drill = raw.get("drill") or {}
    last = drill.get("last_run_at")
    if not isinstance(last, str):
        return None
    try:
        return datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------------
# Portable one-file backup (M12 portable backup, Phase 1: export + encrypted download). Restore
# (upload/validate/wipe/import) is Phase 2, appended further below.
# --------------------------------------------------------------------------------------------------

_EXPORT_MIN_INTERVAL_SECONDS = 60  # backend rate-limit: don't allow back-to-back build storms


class BackupExportDownloadRequest(BaseModel):
    """Download body. The passphrase is POSTed (never a URL/query) so it can't leak into access
    logs; it is piped to openssl via stdin and is never written to disk or logged."""

    passphrase: str = PydField(min_length=8, max_length=1024)


def _export_dir(request: Request) -> Path:
    """The WRITABLE staging dir for portable exports. Defaults to ``<backup_dir>/exports`` but is
    overridable (the backend mounts backup_dir read-only, so deployments point this at a writable
    volume via DOKTOK_BACKUP_EXPORT_DIR)."""
    settings = request.app.state.settings
    configured = (settings.backup_export_dir or "").strip()
    if configured:
        return Path(configured)
    return Path(f"{settings.backup_dir.rstrip('/')}/exports")


def _export_paths(request: Request, export_dir: Path) -> ExportPaths:
    from doktok_api import __version__

    settings = request.app.state.settings
    return ExportPaths(
        export_dir=export_dir,
        files_root=Path(settings.files_root),
        database_url=settings.database_url,
        secrets_key=settings.secrets_key,
        app_version=__version__,
        app_schema_version=_running_schema_version(),
    )


def _record_portable(
    audit: AuditLogRepository,
    tenant_id: str,
    *,
    actor: str,
    ok: bool,
    description: str,
    export_id: str,
) -> None:
    """Mirror a portable-export lifecycle event into the activity log (M12 portable backup Phase 1).

    The authoritative DRP history.jsonl is host-written and the backend mounts it read-only, so the
    backend cannot append there; instead portable events are recorded into the activity log using
    the existing BackupEvent vocabulary with a ``portable`` leg marker. Never carries a secret.
    ``actor`` is the authenticated caller's identity (#560).
    """
    record_activity(
        audit,
        tenant_id,
        AuditEventType.BACKUP_COMPLETED if ok else AuditEventType.BACKUP_FAILED,
        actor=actor,
        actor_kind="user",
        description=description,
        details={"setting": "backup", "leg": "portable", "export_id": export_id},
    )


@router.post("/backup/export", response_model=BackupExportInfo)
def start_backup_export(
    request: Request, tenant: Tenant, audit: Audit, background: BackgroundTasks
) -> BackupExportInfo:
    """Start an ASYNC portable backup build and return immediately with status='building' (M12
    portable backup, Phase 1). Single-flight + rate-limited: 429 if a build is already running or
    one started in the last minute. The build is read-only (pg_dump + a read of files_root)."""
    import uuid

    from doktok_core.backup import export as export_mod

    export_dir = _export_dir(request)
    # Single-flight: never run two concurrent builds (they are heavy and write the same dir).
    if export_mod.is_build_in_progress(export_dir):
        raise HTTPException(status_code=429, detail="a backup export is already building")
    # Rate-limit: don't allow back-to-back rebuild storms.
    latest = export_mod.latest_export_status(export_dir)
    if latest is not None and latest.created_at is not None:
        age = (datetime.now(UTC) - latest.created_at).total_seconds()
        if age < _EXPORT_MIN_INTERVAL_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"a backup export started {int(age)}s ago; "
                f"wait {_EXPORT_MIN_INTERVAL_SECONDS}s",
            )

    export_mod.sweep_stale_exports(
        export_dir
    )  # opportunistic TTL cleanup of old plaintext archives
    export_id = uuid.uuid4().hex
    paths = _export_paths(request, export_dir)
    settings = request.app.state.settings
    app_version = paths.app_version

    def _run() -> None:
        result = export_mod.build_export(paths, export_id)
        ok = result.status == "ready"
        desc = (
            f"Portable backup export ready ({result.member_count} members)"
            if ok
            else f"Portable backup export failed: {result.error}"
        )
        _record_portable(
            audit, tenant.tenant_id, actor=caller, ok=ok, description=desc, export_id=export_id
        )

    caller = actor_identity(tenant)
    background.add_task(_run)
    _record_portable(
        audit,
        tenant.tenant_id,
        actor=caller,
        ok=True,
        description="Portable backup export started",
        export_id=export_id,
    )
    _ = settings
    return BackupExportInfo(
        export_id=export_id,
        status="building",
        created_at=datetime.now(UTC),
        app_version=app_version,
    )


@router.get("/backup/export/status", response_model=BackupExportInfo)
def get_backup_export_status(
    request: Request, tenant: Tenant, export_id: str | None = Query(None)
) -> BackupExportInfo:
    """Poll the status of a portable backup build. With ``export_id`` returns that build; otherwise
    the most recent one. 404 if there is no matching build (M12 portable backup, Phase 1)."""
    _ = tenant
    from doktok_core.backup import export as export_mod

    export_dir = _export_dir(request)
    info = (
        export_mod.read_export_status(export_dir, export_id)
        if export_id
        else export_mod.latest_export_status(export_dir)
    )
    if info is None:
        raise HTTPException(status_code=404, detail="no such backup export")
    return info


@router.post("/backup/export/{export_id}/download")
def download_backup_export(
    export_id: str,
    request: Request,
    tenant: Tenant,
    audit: Audit,
    body: BackupExportDownloadRequest,
) -> StreamingResponse:
    """Stream the staged plaintext archive through ``openssl enc`` and return the AES-256
    ciphertext (M12 portable backup, Phase 1). POST so the passphrase is in the body, never a
    URL/log. The passphrase is piped to openssl on stdin and is never written to disk or logged.
    The download filename ends ``.tgz.enc``; decrypt with the matching ``openssl enc -d``."""
    from doktok_core.backup import export as export_mod

    export_dir = _export_dir(request)
    info = export_mod.read_export_status(export_dir, export_id)
    if info is None or info.status != "ready":
        raise HTTPException(status_code=404, detail="backup export is not ready")
    staged = export_mod.staged_archive_path(export_dir, export_id)
    if not staged.exists():
        raise HTTPException(status_code=404, detail="backup export is not ready")

    stream = _encrypt_stream(staged, body.passphrase)
    ts = (info.created_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    filename = f"doktok-backup-{ts}.tgz.enc"
    _record_portable(
        audit,
        tenant.tenant_id,
        actor=actor_identity(tenant),
        ok=True,
        description="Portable backup export downloaded",
        export_id=export_id,
    )
    return StreamingResponse(
        stream,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _encrypt_stream(staged: Path, passphrase: str) -> Iterator[bytes]:
    """Yield the AES-256 ciphertext of ``staged``.

    The staged plaintext is encrypted by ``openssl enc`` into a sibling temp file (``-in``/``-out``)
    with the passphrase piped on stdin as a single line (``-pass stdin``) - so the passphrase is
    never on the command line, never written to disk, and never logged. We then stream that temp
    ciphertext file out in bounded chunks and delete it (best-effort) when the response finishes, so
    neither plaintext nor ciphertext is ever buffered whole in memory. Encrypting to a file (rather
    than mixing the passphrase line and data on one stdin pipe) is the robust, openssl-version-
    portable invocation - the passphrase line can never be mistaken for archive bytes.
    """
    import subprocess
    import tempfile

    from doktok_core.backup import export as export_mod

    fd, enc_name = tempfile.mkstemp(dir=str(staged.parent), suffix=".enc")
    enc_path = Path(enc_name)
    os.close(fd)
    os.chmod(enc_path, 0o600)
    try:
        proc = subprocess.run(
            export_mod.encrypt_argv(staged, enc_path),
            input=(passphrase + "\n").encode("utf-8"),
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            enc_path.unlink(missing_ok=True)
            # openssl stderr can be empty or generic; never surface or log the passphrase.
            raise HTTPException(status_code=500, detail="failed to encrypt the backup export")
    except BaseException:
        enc_path.unlink(missing_ok=True)
        raise

    def _iter() -> Iterator[bytes]:
        try:
            with enc_path.open("rb") as fh:
                while chunk := fh.read(1024 * 1024):
                    yield chunk
        finally:
            enc_path.unlink(missing_ok=True)

    return _iter()


# --------------------------------------------------------------------------------------------------
# Portable one-file RESTORE (M12 portable restore, Phase 2: upload -> validate -> wipe -> import).
#
# Topology (LOCKED): the NON-destructive preview/validate runs IN THE BACKEND (it has openssl + the
# pg client). The DESTRUCTIVE apply runs via a ROOT systemd path-unit (deploy/restore-import.sh) -
# the backend NEVER execs root/docker; it only drops a fixed request file (the same argument-free
# request-file pattern as the on-demand drill), so a live backend never runs pg_restore --clean on
# the DB it is connected to. This is the most dangerous feature in the app; every guardrail matters.
# --------------------------------------------------------------------------------------------------

# Upload chunk size for streaming the multipart body to disk (never buffer the archive in memory).
_RESTORE_UPLOAD_CHUNK = 4 * 1024 * 1024


def _status_dir(request: Request) -> Path:
    """The host-written status dir (DRP sentinels + the restore status + the request files). This is
    the read-only-mounted backup dir's ``status/`` - the backend reads sentinels here and writes
    request files into ``status/requests/`` (a writable sub-path provisioned for it)."""
    settings = request.app.state.settings
    return Path(f"{settings.backup_dir.rstrip('/')}/status")


def _running_schema_version() -> int:
    """The running code's DB schema generation (latest migration number) for the restore version
    gate. Read from the shipped migrations dir; 0 (gate disabled) if it cannot be located."""
    from doktok_core.backup.schema import schema_version_from_migrations

    try:
        import doktok_storage_postgres.db as pg_db

        return schema_version_from_migrations(pg_db.MIGRATIONS_DIR)
    except Exception:  # noqa: BLE001 - a missing migrations dir disables the gate, never crashes
        return 0


def _record_restore(
    audit: AuditLogRepository,
    tenant_id: str,
    event_type: AuditEventType,
    *,
    actor: str,
    description: str,
    restore_id: str = "",
    staged_id: str = "",
) -> None:
    """Audit a restore lifecycle event into the activity log (M12 portable restore Phase 2). Never
    carries the passphrase, a secret, a DSN, or a host path - only ids + a short description.
    ``actor`` is the authenticated caller's identity (#560)."""
    record_activity(
        audit,
        tenant_id,
        event_type,
        actor=actor,
        actor_kind="user",
        description=description,
        details={
            "setting": "backup",
            "leg": "restore",
            "restore_id": restore_id,
            "staged_id": staged_id,
        },
    )


@router.post("/backup/restore/preview", response_model=RestorePreview)
async def preview_backup_restore(
    request: Request,
    tenant: Tenant,
    audit: Audit,
    file: Annotated[UploadFile, File()],
    passphrase: Annotated[str, Form(min_length=8, max_length=1024)],
) -> RestorePreview:
    """Upload + NON-destructively validate an encrypted portable archive (M12 portable restore
    Phase 2). Streams the upload to disk (never buffers it in memory), decrypts it with the
    passphrase (stdin only; never logged), safely extracts it behind the hostile-archive gate, and
    verifies the manifest + version compatibility. Returns a RestorePreview; on a hard failure
    ``ok`` is False with ``errors`` populated and NO apply is allowed. This route is EXEMPT from the
    global body-size limit and is capped instead by DOKTOK_MAX_RESTORE_GB (413 if larger)."""
    import uuid

    from doktok_core.backup import restore as restore_mod

    settings = request.app.state.settings
    export_dir = _export_dir(request)
    staged_id = uuid.uuid4().hex
    sdir = restore_mod.staging_dir(export_dir, staged_id)
    enc = restore_mod.upload_path(export_dir, staged_id)
    # A non-positive cap means "uploads disabled" (0 bytes allowed -> any upload is a 413).
    max_bytes = max(0, settings.max_restore_gb) * 1024 * 1024 * 1024

    restore_mod.sweep_stale_restores(export_dir)  # opportunistic TTL cleanup of old decrypted trees

    # Stream the upload to disk in bounded chunks, enforcing the size cap as we go (a client can lie
    # about / omit Content-Length, so cap the actual streamed bytes, not just the header).
    try:
        sdir.mkdir(parents=True, exist_ok=True)
        os.chmod(sdir, 0o700)
        written = 0
        fd = os.open(enc, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await file.read(_RESTORE_UPLOAD_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"upload exceeds the {settings.max_restore_gb} GB restore limit",
                        )
                    out.write(chunk)
        finally:
            await file.close()
    except HTTPException:
        restore_mod.discard_staged(export_dir, staged_id)
        raise
    except OSError:
        restore_mod.discard_staged(export_dir, staged_id)
        raise HTTPException(status_code=503, detail="could not stage the upload") from None

    # Flip the status sentinel to 'validating' (best-effort) so the UI poll reflects work in flight.
    restore_mod.write_restore_status(
        _status_dir(request), state="validating", step="validate", restore_id=staged_id
    )

    result = restore_mod.validate_staged_upload(
        export_dir,
        staged_id,
        passphrase,
        secrets_key=settings.secrets_key,
        running_schema_version=_running_schema_version(),
    )
    # Restore the status to idle after a NON-destructive preview (nothing was applied).
    restore_mod.write_restore_status(_status_dir(request), state="idle")

    _record_restore(
        audit,
        tenant.tenant_id,
        AuditEventType.RESTORE_PREVIEWED,
        actor=actor_identity(tenant),
        description=(
            f"Portable restore previewed: {'valid' if result.ok else 'rejected'} "
            f"({result.member_count} members)"
        ),
        staged_id=staged_id,
    )
    return RestorePreview(
        staged_id=staged_id,
        ok=result.ok,
        compatible=result.compatible,
        app_version=result.app_version,
        pg_version=result.pg_version,
        created_at=result.created_at,
        member_count=result.member_count,
        total_bytes=result.total_bytes,
        secrets_key_match=result.secrets_key_match,
        warnings=result.warnings,
        errors=result.errors,
    )


@router.post("/backup/restore/{staged_id}/apply", response_model=RestoreResult)
def apply_backup_restore(
    staged_id: str,
    body: RestoreApplyRequest,
    request: Request,
    tenant: Tenant,
    audit: Audit,
) -> RestoreResult:
    """Trigger the DESTRUCTIVE apply of a PRE-VALIDATED staged archive (M12 portable restore Phase
    2). Requires ``confirm:true`` (422 otherwise; confirm-to-destroy) and a staged_id that passed
    preview (404/409 otherwise). Single-flight: 409 if a restore is already applying. The backend
    NEVER runs the destructive import - it drops a fixed request file the root systemd path-unit
    consumes; the apply is async and the UI polls GET /backup/restore/status."""
    import json as _json
    import uuid

    from doktok_core.backup import restore as restore_mod

    if not body.confirm:
        raise HTTPException(status_code=422, detail="confirm must be true to apply a restore")

    export_dir = _export_dir(request)
    if not restore_mod.is_validated(export_dir, staged_id):
        # Either it never existed, or it failed preview (the tree is cleaned on failure).
        raise HTTPException(
            status_code=409, detail="no validated staged restore with that id; run preview first"
        )

    status_dir = _status_dir(request)
    requests_dir = status_dir / "requests"
    request_file = requests_dir / "restore.request"

    # Single-flight: refuse if a request is already pending OR a restore is currently applying.
    current = restore_mod.read_restore_status(status_dir)
    if request_file.exists() or current.get("state") == "applying":
        raise HTTPException(status_code=409, detail="a restore is already in progress")

    restore_id = uuid.uuid4().hex
    try:
        requests_dir.mkdir(parents=True, exist_ok=True)
        # The request file carries ONLY the staged_id + restore_id (no passphrase - already
        # decrypted into staging; no DSN/secret). 0600: the host helper reads it as root.
        payload = _json.dumps(
            {
                "staged_id": staged_id,
                "restore_id": restore_id,
                "requested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "actor": tenant.tenant_id,
            }
        )
        request_file.write_text(payload, encoding="utf-8")
        request_file.chmod(0o600)
    except OSError:
        raise HTTPException(status_code=503, detail="could not queue the restore request") from None

    # Mark the status applying immediately so a rapid re-POST is single-flighted even before the
    # host helper picks the request up (the helper overwrites this as it progresses).
    restore_mod.write_restore_status(
        status_dir,
        state="applying",
        step="queued",
        restore_id=restore_id,
        started_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _record_restore(
        audit,
        tenant.tenant_id,
        AuditEventType.RESTORE_REQUESTED,
        actor=actor_identity(tenant),
        description="Portable restore requested (destructive apply queued)",
        restore_id=restore_id,
        staged_id=staged_id,
    )
    return RestoreResult(
        accepted=True,
        restore_id=restore_id,
        detail="restore queued; the system will enter maintenance and apply it",
    )


@router.get("/backup/restore/status", response_model=RestoreStatus)
def get_backup_restore_status(request: Request, tenant: Tenant) -> RestoreStatus:
    """Poll the current portable-restore state (M12 portable restore Phase 2). Sourced from the
    host-written ``restore.json`` sentinel OUTSIDE Postgres (the DB is rewritten mid-restore, so a
    DB-backed status would be unreadable/rolled-back). Returns idle when nothing is in flight."""
    _ = tenant
    from doktok_core.backup import restore as restore_mod

    raw = restore_mod.read_restore_status(_status_dir(request))

    def _ts(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    return RestoreStatus(
        state=raw["state"],
        step=raw["step"],
        detail=raw["detail"],
        restore_id=raw["restore_id"],
        started_at=_ts(raw.get("started_at", "")),
        finished_at=_ts(raw.get("finished_at", "")),
    )
