"""FastAPI dependencies: tenant authentication and lazy composition.

``require_tenant`` enforces bearer-token auth and resolves the caller's tenant (ADR-0008).
Repositories are resolved from the app's DI registry; if nothing is bound (production),
Postgres-backed repositories are created lazily on first use over a single shared database handle,
so the health endpoint and tests that inject in-memory repositories never touch a database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast

from doktok_contracts.ports import (
    AuditLogRepository,
    DocumentRepository,
    EntityRepository,
    IngestionJobRepository,
    Retriever,
)
from doktok_contracts.schemas import TenantContext
from doktok_core.security.auth import resolve_tenant
from fastapi import Depends, Header, HTTPException, Request, status

if TYPE_CHECKING:
    from doktok_storage_postgres import Database

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


def _get_database(request: Request) -> Database:
    database = getattr(request.app.state, "database", None)
    if database is None:
        from doktok_storage_postgres import Database, migrate

        settings = request.app.state.settings
        database = Database(settings.database_url)
        migrate(database)
        request.app.state.database = database
    return database


def get_job_repository(request: Request) -> IngestionJobRepository:
    registry = request.app.state.registry
    if registry.is_registered(IngestionJobRepository):
        return cast(IngestionJobRepository, registry.resolve(IngestionJobRepository))

    from doktok_storage_postgres import PostgresIngestionJobRepository

    repository = PostgresIngestionJobRepository(_get_database(request))
    registry.register(IngestionJobRepository, repository)
    return repository


def get_document_repository(request: Request) -> DocumentRepository:
    registry = request.app.state.registry
    if registry.is_registered(DocumentRepository):
        return cast(DocumentRepository, registry.resolve(DocumentRepository))

    from doktok_storage_postgres import PostgresDocumentRepository

    repository = PostgresDocumentRepository(_get_database(request))
    registry.register(DocumentRepository, repository)
    return repository


def get_audit_repository(request: Request) -> AuditLogRepository:
    registry = request.app.state.registry
    if registry.is_registered(AuditLogRepository):
        return cast(AuditLogRepository, registry.resolve(AuditLogRepository))

    from doktok_storage_postgres import PostgresAuditLogRepository

    repository = PostgresAuditLogRepository(_get_database(request))
    registry.register(AuditLogRepository, repository)
    return repository


def get_entity_repository(request: Request) -> EntityRepository:
    registry = request.app.state.registry
    if registry.is_registered(EntityRepository):
        return cast(EntityRepository, registry.resolve(EntityRepository))

    from doktok_storage_postgres import PostgresEntityRepository

    repository = PostgresEntityRepository(_get_database(request))
    registry.register(EntityRepository, repository)
    return repository


def get_retriever(request: Request) -> Retriever:
    registry = request.app.state.registry
    if registry.is_registered(Retriever):
        return cast(Retriever, registry.resolve(Retriever))

    from doktok_provider_ollama import OllamaEmbeddingProvider
    from doktok_retrieval_hybrid import HybridPostgresRetriever

    settings = request.app.state.settings
    retriever = HybridPostgresRetriever(
        _get_database(request),
        OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_base_url),
    )
    registry.register(Retriever, retriever)
    return retriever


Tenant = Annotated[TenantContext, Depends(require_tenant)]
