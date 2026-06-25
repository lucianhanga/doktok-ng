"""DokTok NG FastAPI backend.

Exposes a health endpoint and the ingestion job API, and wires application settings and the DI
registry. Document, search, and chat routes arrive in later milestones.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from doktok_contracts.schemas import HealthStatus
from doktok_core.config import Settings, get_settings
from doktok_core.registry import Registry, build_registry
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from doktok_api import __version__
from doktok_api.dependencies import Tenant, get_app_settings_repository
from doktok_api.routers import (
    aggregate,
    audit,
    categories,
    chat,
    documents,
    entities,
    features,
    ingestion,
    search,
    stats,
    tokens,
    visualizations,
)
from doktok_api.routers import (
    settings as settings_router,
)

logger = logging.getLogger("doktok.api")
SERVICE_NAME = "doktok-ng-backend"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# A worker heartbeat older than this marks the worker as stale in /ready (APP-5). The worker beats
# every ~15s, so 120s tolerates a few missed beats / a slow scan without false alarms.
_WORKER_STALE_SECONDS = 120


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _record_service_started(app, actor="backend")
    yield
    # Close a lazily-created database pool, if one was opened during the app's lifetime.
    database = getattr(app.state, "database", None)
    if database is not None:
        database.close()


def _record_service_started(app: FastAPI, *, actor: str) -> None:
    """Append a 'service.started' activity row per tenant on startup (M15 #373). Best-effort: a
    brief connection just for this write, skipped under tests; failures never block startup."""
    settings = app.state.settings
    if settings.env == "test":
        return
    try:
        from doktok_contracts.schemas import AuditEventType
        from doktok_core.audit.logger import record_activity
        from doktok_storage_postgres import Database, PostgresAuditLogRepository

        db = Database(settings.database_url)
        try:
            repo = PostgresAuditLogRepository(db)
            for tenant in dict.fromkeys(settings.tenant_tokens.values()):
                record_activity(
                    repo,
                    tenant,
                    AuditEventType.SERVICE_STARTED,
                    actor=actor,
                    actor_kind="system",
                    description=f"{actor.capitalize()} started",
                )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - a startup activity row must never block the service
        logger.warning("failed to record service-started activity", exc_info=True)


def create_app(settings: Settings | None = None, registry: Registry | None = None) -> FastAPI:
    """Application factory.

    Accepts optional ``settings`` and ``registry`` for testing/overrides; otherwise builds the
    defaults from the environment.
    """
    settings = settings or get_settings()
    registry = registry or build_registry()

    from doktok_core.logging_setup import configure_logging

    configure_logging(json_format=settings.log_format == "json", level=settings.log_level)

    # Fail-closed: never expose a non-loopback bind without configured tokens (ADR-0008).
    if settings.bind_host not in _LOOPBACK_HOSTS and not settings.tenant_tokens:
        raise RuntimeError(
            f"refusing to bind non-loopback host {settings.bind_host!r} without auth tokens; "
            "set DOKTOK_TENANT_TOKENS or bind to loopback"
        )

    app = FastAPI(
        title="DokTok NG",
        version=__version__,
        summary="Local-first document-intelligence system",
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.registry = registry

    # CORS origins are configurable (APP-10; loopback dev origins by default). The bearer token is
    # the real control (ADR-0008); allow_credentials stays False (header auth, no cookie CSRF).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Per-token rate limiter (APP-9); only active when configured (>0).
    from doktok_api.metrics import Metrics
    from doktok_api.ratelimit import RateLimiter

    app.state.rate_limiter = (
        RateLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    )
    app.state.metrics = Metrics()  # APP-13
    _max_body_bytes = settings.max_request_mb * 1024 * 1024
    _exempt_paths = frozenset({"/health", "/ready", "/metrics"})

    @app.middleware("http")
    async def _limits(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Reject oversized bodies up front (APP-10).
        cl = request.headers.get("Content-Length")
        if cl is not None and cl.isdigit() and int(cl) > _max_body_bytes:
            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        # Per-token rate limit (APP-9); health/ready/metrics are exempt so probes aren't throttled.
        limiter = request.app.state.rate_limiter
        if limiter is not None and request.url.path not in _exempt_paths:
            auth = request.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            if token:
                allowed, retry_after = limiter.allow(token)
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "rate limit exceeded"},
                        headers={"Retry-After": str(retry_after)},
                    )
        # Record request metrics (APP-13).
        import time as _time

        t0 = _time.monotonic()
        response = await call_next(request)
        request.app.state.metrics.observe(
            request.method, response.status_code, _time.monotonic() - t0
        )
        return response

    @app.middleware("http")
    async def _request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Correlation id: echo the caller's X-Request-ID or mint one, so logs/responses can be tied
        # together. (Logging hookup can consume request.state.request_id later.)
        from doktok_core.logging_setup import request_id_var

        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        request_id_var.set(request_id)  # correlate log lines for this request (APP-12)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health", response_model=HealthStatus, tags=["system"])
    def health() -> HealthStatus:
        return HealthStatus(
            status="ok",
            service=SERVICE_NAME,
            version=__version__,
            environment=settings.env,
        )

    @app.get("/ready", tags=["system"])
    def ready(request: Request) -> JSONResponse:
        # Liveness (/health) never touches dependencies. Readiness probes each dependency so an
        # orchestrator/proxy routes traffic only when the instance can serve. HARD deps (DB, the
        # local embedder) fail the probe (503); SOFT deps (Gotenberg, and OpenAI when selected) are
        # reported but do not deroute the instance. Every check is bounded so the probe can't hang.
        import httpx
        from doktok_core.security.egress import openai_egress_allowed

        from doktok_api.dependencies import _get_database

        checks: list[dict[str, object]] = []

        def _record(name: str, hard: bool, ok: bool, detail: str = "") -> None:
            checks.append(
                {
                    "name": name,
                    "hard": hard,
                    "status": "ok" if ok else "down",
                    "detail": detail[:200],
                }
            )

        def _http_ok(
            url: str, *, headers: dict[str, str] | None = None, timeout: float = 2.0
        ) -> tuple[bool, str]:
            try:
                resp = httpx.get(url, headers=headers, timeout=timeout)
                return (
                    resp.status_code < 500,
                    "" if resp.status_code < 500 else f"HTTP {resp.status_code}",
                )
            except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
                return (False, str(exc))

        # DB (hard)
        try:
            with _get_database(request).connection() as conn:
                conn.execute("SELECT 1")
            _record("database", True, True)
        except Exception as exc:  # noqa: BLE001
            _record("database", True, False, str(exc))

        # Embedding Ollama endpoint (hard - ingest + RAG retrieval need it). Check the *effective*
        # endpoint (per-purpose override or default), so offloaded embeddings stay green and a
        # stopped local Ollama (when unused, M16 #374) does not fail readiness.
        try:
            embedding_url = (
                get_app_settings_repository(request).get_ai_settings().embedding.ollama_base_url
                or settings.ollama_base_url
            )
        except Exception:  # noqa: BLE001 - fall back to the default if settings can't be read
            embedding_url = settings.ollama_base_url
        ok, detail = _http_ok(f"{embedding_url.rstrip('/')}/api/tags")
        _record("ollama", True, ok, detail)

        # Gotenberg (soft - only office-doc ingest needs it)
        ok, detail = _http_ok(f"{settings.gotenberg_url.rstrip('/')}/health")
        _record("gotenberg", False, ok, detail)

        # Worker heartbeat (soft) and OpenAI reachability (soft, only when selected).
        try:
            from datetime import UTC, datetime

            app_settings = get_app_settings_repository(request)

            beat = app_settings.get_worker_heartbeat()
            if beat is None:
                _record("worker", False, False, "no heartbeat recorded yet")
            else:
                age = (datetime.now(UTC) - beat).total_seconds()
                _record("worker", False, age <= _WORKER_STALE_SECONDS, f"last beat {int(age)}s ago")

            ai = app_settings.get_ai_settings()
            key = app_settings.get_openai_api_key() or settings.openai_api_key
            selected = "openai" in (ai.pipeline.provider, ai.rag.provider)
            if selected and openai_egress_allowed(key=key, no_egress=settings.no_egress):
                ok, detail = _http_ok(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=3.0,
                )
                _record("openai", False, ok, detail)
        except Exception as exc:  # noqa: BLE001
            _record("worker", False, False, str(exc))

        ready_ok = all(c["status"] == "ok" for c in checks if c["hard"])
        return JSONResponse(
            status_code=200 if ready_ok else 503,
            content={"status": "ready" if ready_ok else "unavailable", "checks": checks},
        )

    @app.get("/metrics", tags=["system"])
    def metrics(request: Request, tenant: Tenant) -> Response:
        # Token-gated (APP-13). Request counters/latency + worker heartbeat age + uptime, in the
        # Prometheus text format. Exempt from rate limiting; scrapers poll it frequently.
        _ = tenant
        from datetime import UTC, datetime

        m = request.app.state.metrics
        gauges: dict[str, float] = {"doktok_uptime_seconds": round(m.uptime_seconds(), 1)}
        try:
            repo = get_app_settings_repository(request)
            beat = repo.get_worker_heartbeat()
            if beat is not None:
                gauges["doktok_worker_heartbeat_age_seconds"] = round(
                    (datetime.now(UTC) - beat).total_seconds(), 1
                )
            # Backup freshness from the same sentinels the DRP panel reads (#368/#357).
            backup = repo.get_backup_status()
            gauges["doktok_backup_status_source_available"] = 1.0 if backup is not None else 0.0
            for leg in ("files", "pg", "offsite", "drill"):
                raw = (backup or {}).get(leg) or {}
                ts = raw.get("last_run_at")
                if isinstance(ts, str):
                    try:
                        beat_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        gauges[f"doktok_backup_{leg}_age_seconds"] = round(
                            (datetime.now(UTC) - beat_at).total_seconds(), 1
                        )
                    except ValueError:
                        pass
        except Exception:  # noqa: BLE001 - metrics must never raise
            pass
        return Response(content=m.render(gauges), media_type="text/plain; version=0.0.4")

    app.include_router(ingestion.router)
    app.include_router(documents.router)
    app.include_router(audit.router)
    app.include_router(search.router)
    app.include_router(entities.router)
    app.include_router(stats.router)
    app.include_router(chat.router)
    app.include_router(tokens.router)
    app.include_router(features.router)
    app.include_router(categories.router)
    app.include_router(aggregate.router)
    app.include_router(visualizations.router)
    app.include_router(settings_router.router)

    return app


app = create_app()
