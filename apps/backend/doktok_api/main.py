"""DokTok NG FastAPI backend (M0 skeleton).

Exposes a health endpoint and wires the application settings and DI registry. Real document,
ingestion, search, and chat routes arrive in later milestones.
"""

from __future__ import annotations

from doktok_contracts.schemas import HealthStatus
from doktok_core.config import Settings, get_settings
from doktok_core.registry import Registry, build_registry
from fastapi import FastAPI

from doktok_api import __version__

SERVICE_NAME = "doktok-ng-backend"


def create_app(settings: Settings | None = None, registry: Registry | None = None) -> FastAPI:
    """Application factory.

    Accepts optional ``settings`` and ``registry`` for testing/overrides; otherwise builds the
    defaults from the environment.
    """
    settings = settings or get_settings()
    registry = registry or build_registry()

    app = FastAPI(
        title="DokTok NG",
        version=__version__,
        summary="Local-first document-intelligence system",
    )
    app.state.settings = settings
    app.state.registry = registry

    @app.get("/health", response_model=HealthStatus, tags=["system"])
    def health() -> HealthStatus:
        return HealthStatus(
            status="ok",
            service=SERVICE_NAME,
            version=__version__,
            environment=settings.env,
        )

    return app


app = create_app()
