"""DokTok NG FastAPI backend.

Exposes a health endpoint and the ingestion job API, and wires application settings and the DI
registry. Document, search, and chat routes arrive in later milestones.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import HealthStatus
from doktok_core.config import Settings, get_settings
from doktok_core.registry import Registry, build_registry
from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import State

from doktok_api import __version__
from doktok_api.dependencies import AdminUser, get_app_settings_repository
from doktok_api.routers import (
    admin,
    aggregate,
    audit,
    auth,
    categories,
    chat,
    documents,
    entities,
    features,
    ingestion,
    preferences,
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
# F-42 (#654): after the maintenance flag was seen in-process, a stat error keeps the gate closed
# for this long (a broken mount mid-restore must not let mutations through).
_MAINTENANCE_RECENT_SECONDS = 3600.0


def _check_login_secret(settings: Settings) -> None:
    """Warn (loudly) about a weak or cross-purpose JWT signing secret when login is enabled (CISO
    M5). HS256 is only as strong as its secret; a short one is offline-crackable from any captured
    token. Local-first dev boxes only get the warning (never wedge a developer); everywhere else a
    short dedicated secret DISABLES login (F-35, #647 - see effective_jwt_secret) and gets an
    error. The operator should mint a dedicated ``DOKTOK_AUTH_JWT_SECRET``
    (openssl rand -base64 48)."""
    from doktok_api.dependencies import MIN_JWT_SECRET_BYTES, WEAK_SECRET_EXEMPT_ENVS

    if settings.auth_jwt_secret:
        if len(settings.auth_jwt_secret.encode()) < MIN_JWT_SECRET_BYTES:
            if settings.env not in WEAK_SECRET_EXEMPT_ENVS:
                logger.error(
                    "DOKTOK_AUTH_JWT_SECRET is shorter than %d bytes and env=%r is not a dev "
                    "environment - LOGIN IS DISABLED (F-35). Set a longer random secret "
                    "(openssl rand -base64 48) to re-enable it",
                    MIN_JWT_SECRET_BYTES,
                    settings.env,
                )
            else:
                logger.warning(
                    "DOKTOK_AUTH_JWT_SECRET is shorter than %d bytes; use a longer random secret "
                    "(openssl rand -base64 48) - short HS256 secrets are offline-crackable",
                    MIN_JWT_SECRET_BYTES,
                )
    elif settings.secrets_key:
        logger.warning(
            "login is signing JWTs with DOKTOK_SECRETS_KEY (no dedicated DOKTOK_AUTH_JWT_SECRET); "
            "reusing the envelope-encryption key for signing widens the blast radius of a leak - "
            "set a dedicated DOKTOK_AUTH_JWT_SECRET"
        )


def _maintenance_active(settings: Settings, state: State | None = None) -> bool:
    """True iff the host-written maintenance sentinel exists (M12 portable restore Phase 2).

    The destructive restore helper (deploy/restore-import.sh) drops
    ``<backup_dir>/status/maintenance.flag`` before it touches the DB/files and removes it on
    success (a failed restore leaves it ON until a human clears it - fail safe). The flag is a FILE,
    not the DB ``maintenance_mode`` row, because the DB is being rewritten mid-restore and a
    DB-backed flag would be unreadable / rolled back. On a stat error: fail CLOSED for
    ``_MAINTENANCE_RECENT_SECONDS`` after the flag was last seen in this process (F-42, #654) -
    a broken mount mid-restore must not let mutations through; never-seen + error stays
    fail-open so a stat failure can never wedge the whole API closed."""
    from pathlib import Path

    try:
        flag = Path(f"{settings.backup_dir.rstrip('/')}/status/maintenance.flag")
        active = flag.exists()
        if active and state is not None:
            state._maintenance_last_seen = time.monotonic()
        return active
    except OSError:
        last_seen = getattr(state, "_maintenance_last_seen", None) if state is not None else None
        return last_seen is not None and time.monotonic() - last_seen < _MAINTENANCE_RECENT_SECONDS


_REQUEST_ID_MAX_LEN = 64
_REQUEST_ID_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _safe_request_id(raw: str | None) -> str | None:
    """Return the caller's X-Request-ID when it is safe to echo + put into the log contextvar
    (F-42, #654); else None so a fresh one is minted. Bounded length and a printable-token
    charset - the HTTP parser already blocks CR/LF, this stops log forging with printable junk
    and unbounded log bloat."""
    if not raw or len(raw) > _REQUEST_ID_MAX_LEN:
        return None
    return raw if all(c in _REQUEST_ID_SAFE for c in raw) else None


def _warm_tenant_registry(app: FastAPI) -> None:
    """Register the DB-backed TenantRegistry at startup (F-23, #637).

    The registry was built lazily on the first auth/admin request; until then the per-request
    deactivation check was skipped, so a deactivated user's unexpired JWT (<=1 h TTL) kept tenant
    read access after a restart - potentially for hours on a quiet box. Skipped under tests (test
    apps pass their own registry) and when a registry is already present; any failure logs a
    warning and leaves the lazy request-time path as the fallback - startup is never blocked."""
    settings = app.state.settings
    if settings.env == "test":
        return
    registry = app.state.registry
    if registry.is_registered(TenantRegistry):
        return
    try:
        from doktok_storage_postgres import PostgresTenantRegistry

        from doktok_api.dependencies import open_database

        database = open_database(settings)
        app.state.database = database  # request-time resolution reuses this one pool
        registry.register(TenantRegistry, PostgresTenantRegistry(database))
    except Exception:  # noqa: BLE001 - never block startup; the lazy path is the fallback
        logger.warning("failed to pre-warm the tenant registry", exc_info=True)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _record_service_started(app, actor="backend")
    _warm_tenant_registry(app)
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
    import threading

    from doktok_core.security.auth import hash_token

    from doktok_api.metrics import Metrics
    from doktok_api.ratelimit import RateLimiter

    app.state.rate_limiter = (
        RateLimiter(settings.rate_limit_per_minute) if settings.rate_limit_per_minute > 0 else None
    )
    # Pre-auth login throttle (CISO M2): per-IP and per-(tenant, email) buckets + a semaphore that
    # caps concurrent scrypt verifications. Independent of the per-token limiter (login has no token
    # yet), so it protects the unauthenticated endpoint the API limiter never sees.
    app.state.login_ip_limiter = (
        RateLimiter(settings.login_ip_rate_per_minute)
        if settings.login_ip_rate_per_minute > 0
        else None
    )
    app.state.login_acct_limiter = (
        RateLimiter(settings.login_rate_per_minute) if settings.login_rate_per_minute > 0 else None
    )
    app.state.login_verify_semaphore = threading.Semaphore(
        max(1, settings.login_max_concurrent_verifies)
    )
    # Global cap on concurrent answer generations (#626, F-14): chat/SSE requests 429 when full.
    app.state.chat_semaphore = threading.Semaphore(settings.chat_max_concurrent)
    _check_login_secret(settings)
    app.state.metrics = Metrics()  # APP-13
    _max_body_bytes = settings.max_request_mb * 1024 * 1024
    _max_upload_bytes = settings.max_upload_mb * 1024 * 1024
    _exempt_paths = frozenset({"/health", "/ready", "/metrics"})
    # The portable-restore preview streams a multi-GB encrypted archive to disk, so it is EXEMPT
    # from the global JSON body-size cap and is instead bounded by DOKTOK_MAX_RESTORE_GB inside the
    # route (a 413 there).
    _restore_preview_path = "/api/v1/settings/backup/restore/preview"
    # Document upload is a multi-file batch bounded by max_upload_mb (larger than the JSON cap) so a
    # big drop of small files goes through; per-file is still capped in the route.
    _upload_path = "/api/v1/ingestion/upload"

    @app.middleware("http")
    async def _limits(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Maintenance mode (M12 portable restore Phase 2): while a destructive restore is applying,
        # a host-written sentinel file (OUTSIDE Postgres, since the DB is being rewritten) parks all
        # mutating requests with a 503 so nothing writes into a half-restored system. Read-only GETs
        # and the restore status poll itself stay available.
        if request.method not in ("GET", "HEAD", "OPTIONS") and _maintenance_active(
            settings, request.app.state
        ):
            return JSONResponse(
                status_code=503,
                content={"detail": "service is in maintenance (a restore is in progress)"},
                headers={"Retry-After": "30"},
            )
        # Body-size enforcement (APP-10 + F-05): the restore preview is exempt (capped in-route);
        # document upload uses the larger batch cap; everything else the JSON cap. A request
        # WITHOUT a valid Content-Length (chunked, or a garbage/absurd header) previously bypassed
        # the cap and was fully buffered in RAM before validation - including pre-auth on
        # /auth/login (F-05), and a >4300-digit header crashed this middleware (F-27). Fail closed
        # BEFORE any buffering: 411 when the length is missing, 400 when malformed, 413 when over.
        if request.url.path == _restore_preview_path:
            body_limit: int | None = None
        elif request.url.path == _upload_path:
            body_limit = _max_upload_bytes
        else:
            body_limit = _max_body_bytes
        if body_limit is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
            cl = request.headers.get("Content-Length")
            if cl is None:
                # A request body is signaled by Transfer-Encoding or Content-Length (RFC 9112):
                # neither header means NO body (DELETEs, body-less POSTs), which is fine. A body
                # sent chunked carries no length and previously bypassed the cap, getting fully
                # buffered in RAM - including pre-auth on /auth/login (F-05). Fail closed first.
                if request.headers.get("Transfer-Encoding"):
                    return JSONResponse(
                        status_code=411,
                        content={"detail": "Content-Length header required (no chunked bodies)"},
                    )
            elif not cl.isdigit():
                return JSONResponse(
                    status_code=400, content={"detail": "invalid Content-Length header"}
                )
            elif len(cl) > 18 or int(cl) > body_limit:  # 18 digits bounds int() cost (F-27)
                return JSONResponse(status_code=413, content={"detail": "request body too large"})
        # Per-token rate limit (APP-9); health/ready/metrics are exempt so probes aren't throttled.
        # The bucket key is the token's sha256 (F-06): fixed size regardless of the presented
        # token's length, and no credential material kept in memory.
        limiter = request.app.state.rate_limiter
        if limiter is not None and request.url.path not in _exempt_paths:
            auth = request.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else ""
            if token:
                allowed, retry_after = limiter.allow(hash_token(token))
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
        # together. Caller-supplied ids are length/charset-checked (F-42, #654). (Logging hookup
        # can consume request.state.request_id later.)
        from doktok_core.logging_setup import request_id_var

        request_id = _safe_request_id(request.headers.get("X-Request-ID")) or uuid.uuid4().hex
        request.state.request_id = request_id
        request_id_var.set(request_id)  # correlate log lines for this request (APP-12)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Baseline hardening headers on EVERY response (F-22, #636) - registered last so it wraps
        # the other middlewares and also covers their early rejections (401/413/429/503). The API
        # serves JSON + file bytes but no HTML, so the CSP denies all sources; frame-ancestors
        # 'self' (and XFO SAMEORIGIN as the legacy fallback) keeps the UI's same-origin PDF
        # preview <iframe> working. setdefault: endpoint-specific values always win. HSTS is
        # emitted only when the request arrived over HTTPS (edge TLS forwarded by Caddy) -
        # plain-HTTP dev/test deployments must not pin TLS they don't terminate.
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'self'"
        )
        forwarded = request.headers.get("x-forwarded-proto", request.url.scheme)
        if forwarded.split(",")[0].strip().lower() == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.get("/health", response_model=HealthStatus, tags=["system"])
    def health() -> HealthStatus:
        return HealthStatus(
            status="ok",
            service=SERVICE_NAME,
            version=__version__,
            environment=settings.env,
        )

    # Readiness result cache (#625, F-13): each level computes at most once per window, so a probe
    # flood coalesces instead of fanning out per call.
    _READY_CACHE_SECONDS = 5.0
    app.state._ready_shallow_cache = None
    app.state._ready_deep_cache = None

    def _ready_shallow(request: Request) -> JSONResponse:
        # Shallow probe: process-up + the hard DB check with STATIC details - no exception text,
        # no internal addresses, and NO outbound HTTP fan-out (#625, F-13).
        from doktok_api.dependencies import _get_database

        checks: list[dict[str, object]] = []
        try:
            with _get_database(request).connection() as conn:
                conn.execute("SELECT 1")
            checks.append({"name": "database", "hard": True, "status": "ok", "detail": ""})
        except Exception:  # noqa: BLE001 - readiness reports, never raises
            checks.append(
                {"name": "database", "hard": True, "status": "down", "detail": "unavailable"}
            )
        ok = all(c["status"] == "ok" for c in checks if c["hard"])
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "unavailable", "checks": checks},
        )

    def _ready_deep(request: Request) -> JSONResponse:
        # Deep probe (authenticated): the full dependency fan-out with details. Liveness (/health)
        # never touches dependencies. Readiness probes each dependency so an orchestrator/proxy
        # routes traffic only when the instance can serve. HARD deps (DB, the local embedder) fail
        # the probe (503); SOFT deps (Gotenberg, and OpenAI when selected) are reported but do not
        # deroute the instance. Every check is bounded so the probe can't hang.
        import httpx
        from doktok_core.security.egress import effective_no_egress, openai_egress_allowed

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
            no_egress = effective_no_egress(
                app_settings.get_no_egress(),
                env_default=settings.no_egress,
                lock=settings.no_egress_lock,
            )
            if selected and openai_egress_allowed(key=key, no_egress=no_egress):
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

    @app.get("/ready", tags=["system"])
    def ready(request: Request, deep: bool = False) -> JSONResponse:
        # Two levels (#625, F-13): shallow (default, public) = process + DB with static details and
        # no outbound probes, so a /ready flood cannot pin the threadpool or leak internals. Deep
        # (?deep=1, authenticated) = the full dependency fan-out with details. Both cached briefly.
        import time as _time

        state = request.app.state
        now = _time.monotonic()
        attr = "_ready_deep_cache" if deep else "_ready_shallow_cache"
        cached: tuple[float, JSONResponse] | None = getattr(state, attr, None)
        if cached is not None and now - cached[0] < _READY_CACHE_SECONDS:
            return cached[1]
        if deep:
            from doktok_api.dependencies import require_tenant

            require_tenant(request, request.headers.get("authorization"))
        response = _ready_deep(request) if deep else _ready_shallow(request)
        setattr(state, attr, (now, response))
        return response

    @app.get("/metrics", tags=["system"])
    def metrics(request: Request, tenant: AdminUser) -> Response:
        # Admin-gated (F-19, #633): metrics expose host topology, backup cadence, and heartbeat
        # state. Static tenant tokens resolve to admin, so scrapers keep working. Exempt from
        # rate limiting; scrapers poll it frequently.
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

    # RBAC (#556): a method-aware guard applied per router. Reads (GET/HEAD) pass for any
    # authenticated caller; writes require the role below. Content routers require 'editor'. The
    # auth router is intentionally unguarded - login is public and /auth/me is already user-gated.
    # A tenant-scoped token (no user) resolves to admin, so local-first single-operator deployments
    # are unaffected (see resolve_caller_role).
    from doktok_core.security.roles import Role

    from doktok_api.dependencies import make_write_guard, require_admin

    editor_writes = [Depends(make_write_guard(Role.EDITOR))]

    app.include_router(auth.router)
    # Administration (#559): admin-only for EVERY method (listings included), not just writes.
    app.include_router(admin.router, dependencies=[Depends(require_admin)])
    # Per-user preferences (#558): self-scoped read/write for ANY authenticated caller (a viewer
    # sets their own UI prefs), so no role write-guard.
    app.include_router(preferences.router)
    app.include_router(ingestion.router, dependencies=editor_writes)
    app.include_router(documents.router, dependencies=editor_writes)
    app.include_router(audit.router, dependencies=editor_writes)
    app.include_router(search.router, dependencies=editor_writes)
    app.include_router(entities.router, dependencies=editor_writes)
    app.include_router(stats.router, dependencies=editor_writes)
    app.include_router(chat.router, dependencies=editor_writes)
    app.include_router(tokens.router, dependencies=editor_writes)
    app.include_router(features.router, dependencies=editor_writes)
    app.include_router(categories.router, dependencies=editor_writes)
    app.include_router(aggregate.router, dependencies=editor_writes)
    app.include_router(visualizations.router, dependencies=editor_writes)
    # Settings (F-19, #633): admin-only for EVERY method - reads expose host paths, backup cadence,
    # and model topology.
    app.include_router(settings_router.router, dependencies=[Depends(require_admin)])

    return app


app = create_app()
