"""DokTok NG FastAPI backend.

Exposes a health endpoint and the ingestion job API, and wires application settings and the DI
registry. Document, search, and chat routes arrive in later milestones.
"""

from __future__ import annotations

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

SERVICE_NAME = "doktok-ng-backend"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# A worker heartbeat older than this marks the worker as stale in /ready (APP-5). The worker beats
# every ~15s, so 120s tolerates a few missed beats / a slow scan without false alarms.
_WORKER_STALE_SECONDS = 120


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Close a lazily-created database pool, if one was opened during the app's lifetime.
    database = getattr(app.state, "database", None)
    if database is not None:
        database.close()


def create_app(settings: Settings | None = None, registry: Registry | None = None) -> FastAPI:
    """Application factory.

    Accepts optional ``settings`` and ``registry`` for testing/overrides; otherwise builds the
    defaults from the environment.
    """
    settings = settings or get_settings()
    registry = registry or build_registry()

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

    # CORS restricted to loopback dev origins; the bearer token is the real control (ADR-0008).
    # allow_credentials stays False (default): auth is header-based, so CORS is only a secondary
    # control and cookie-based CSRF does not apply.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def _request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Correlation id: echo the caller's X-Request-ID or mint one, so logs/responses can be tied
        # together. (Logging hookup can consume request.state.request_id later.)
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
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

        from doktok_api.dependencies import _get_database, get_app_settings_repository

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

        # Local Ollama embedder (hard - both ingest and RAG retrieval need it)
        ok, detail = _http_ok(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
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
