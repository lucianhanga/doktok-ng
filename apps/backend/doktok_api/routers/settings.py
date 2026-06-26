"""System settings (the Settings tab). Global config; token-protected.

These are single-user system settings (not tenant-scoped) - any authenticated caller reads/writes
them. Changes are persisted and take effect on the next worker/backend restart. The OpenAI key is
write-only: it is never returned, only set/cleared, and GET reports only whether one is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
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
    BackupLegStatus,
    DrillTriggerResponse,
    DrpConfig,
    DrpHistoryResponse,
    DrpStatus,
    DrpStatusResponse,
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
)
from doktok_core.audit.logger import record_activity
from doktok_core.security.egress import openai_egress_allowed
from doktok_core.settings.catalog import MODEL_CATALOG
from doktok_core.settings.ocr_recommend import recommend_ocr
from doktok_core.settings.runtime import local_ollama_needed
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from doktok_api.dependencies import (
    Tenant,
    get_app_settings_repository,
    get_audit_repository,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

Repo = Annotated[AppSettingsRepository, Depends(get_app_settings_repository)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]


def _response(repo: AppSettingsRepository, ai: AiSettings, request: Request) -> AiSettingsResponse:
    settings = request.app.state.settings
    key = repo.get_openai_api_key() or settings.openai_api_key
    remote_selected = "openai" in (ai.pipeline.provider, ai.rag.provider)
    return AiSettingsResponse(
        **ai.model_dump(),
        openai_api_key_set=bool(repo.get_openai_api_key()),
        embedding_model=settings.embedding_model,
        embedding_num_ctx=settings.embedding_num_ctx,
        ollama_base_url_default=settings.ollama_base_url,
        egress_active=remote_selected
        and openai_egress_allowed(key=key, no_egress=settings.no_egress),
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
def ai_model_catalog(tenant: Tenant) -> ModelCatalog:
    """The selectable models per AI purpose + the reasoning-density levels."""
    _ = tenant  # auth-gated; the catalog is the same for everyone
    return MODEL_CATALOG


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
    _validate_ollama_url(update.rag.ollama_base_url, "rag.ollama_base_url")
    _validate_ollama_url(update.embedding.ollama_base_url, "embedding.ollama_base_url")
    ai = AiSettings(pipeline=update.pipeline, rag=update.rag, embedding=update.embedding)
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
        f"RAG {ai.rag.provider}/{ai.rag.model}"
    )
    if key_changed:
        summary += ", OpenAI key updated"
    record_activity(
        audit,
        tenant.tenant_id,
        AuditEventType.SETTINGS_CHANGED,
        actor=tenant.tenant_id,
        actor_kind="user",
        description=summary,
        details={"setting": "ai"},
    )
    return _response(repo, ai, request)


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
        actor=tenant.tenant_id,
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
_HISTORY_LEGS = ("files", "pg", "offsite", "drill", "prune")

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
        if event_type is None:
            continue  # skip start/prune-noise; only terminal outcomes are worth an activity row
        seed = f"{ev.seq}|{ev.ts.isoformat()}|{ev.leg}|{ev.event}"
        event_id = "drp-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        desc = f"{ev.leg} backup {ev.event}"
        if ev.event in ("drill_pass", "drill_fail", "drill_completed"):
            desc = f"restore drill {'passed' if ev.ok else 'failed'}"
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
        actor=tenant.tenant_id,
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
