"""FastAPI dependencies: tenant authentication and lazy composition.

``require_tenant`` enforces bearer-token auth and resolves the caller's tenant (ADR-0008). The job
repository is resolved from the app's DI registry; if nothing is bound (production), a
Postgres-backed repository is created lazily on first use and cached, so the health endpoint and
tests that inject an in-memory repository never touch a database.
"""

from __future__ import annotations

from typing import Annotated, cast

from doktok_contracts.ports import IngestionJobRepository
from doktok_contracts.schemas import TenantContext
from doktok_core.security.auth import resolve_tenant
from fastapi import Depends, Header, HTTPException, Request, status

_BEARER_PREFIX = "Bearer "


def require_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """Authenticate the request and return its tenant. Fail-closed if no tokens are configured."""
    tokens = request.app.state.settings.tenant_tokens
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured (set DOKTOK_TENANT_TOKENS)",
        )
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    presented = authorization[len(_BEARER_PREFIX) :]
    tenant_id = resolve_tenant(tokens, presented)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TenantContext(tenant_id=tenant_id)


def get_job_repository(request: Request) -> IngestionJobRepository:
    registry = request.app.state.registry
    if registry.is_registered(IngestionJobRepository):
        return cast(IngestionJobRepository, registry.resolve(IngestionJobRepository))

    # Lazy production wiring: build a Postgres-backed repository once and cache it.
    from doktok_storage_postgres import (
        Database,
        PostgresIngestionJobRepository,
        migrate,
    )

    settings = request.app.state.settings
    database = Database(settings.database_url)
    migrate(database)
    repository = PostgresIngestionJobRepository(database)
    registry.register(IngestionJobRepository, repository)
    request.app.state.database = database
    return repository


Tenant = Annotated[TenantContext, Depends(require_tenant)]
