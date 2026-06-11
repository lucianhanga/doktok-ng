"""DokTok NG FastAPI backend.

Exposes a health endpoint and the ingestion job API, and wires application settings and the DI
registry. Document, search, and chat routes arrive in later milestones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from doktok_contracts.schemas import HealthStatus
from doktok_core.config import Settings, get_settings
from doktok_core.registry import Registry, build_registry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from doktok_api import __version__
from doktok_api.routers import (
    audit,
    chat,
    documents,
    entities,
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", response_model=HealthStatus, tags=["system"])
    def health() -> HealthStatus:
        return HealthStatus(
            status="ok",
            service=SERVICE_NAME,
            version=__version__,
            environment=settings.env,
        )

    app.include_router(ingestion.router)
    app.include_router(documents.router)
    app.include_router(audit.router)
    app.include_router(search.router)
    app.include_router(entities.router)
    app.include_router(stats.router)
    app.include_router(chat.router)
    app.include_router(tokens.router)

    return app


app = create_app()
