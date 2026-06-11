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
)

SERVICE_NAME = "doktok-ng-backend"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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
        # Liveness (/health) never touches dependencies; readiness checks the DB is reachable so an
        # orchestrator doesn't route traffic to an instance whose Postgres pool is down.
        from doktok_api.dependencies import _get_database

        try:
            with _get_database(request).connection() as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001 - readiness reports, it does not raise
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "detail": str(exc)[:200]}
            )
        return JSONResponse(status_code=200, content={"status": "ready"})

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

    return app


app = create_app()
