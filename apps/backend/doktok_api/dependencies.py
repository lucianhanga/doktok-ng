"""FastAPI dependencies: tenant authentication and lazy composition.

``require_tenant`` enforces bearer-token auth and resolves the caller's tenant (ADR-0008).
Repositories are resolved from the app's DI registry; if nothing is bound (production),
Postgres-backed repositories are created lazily on first use over a single shared database handle,
so the health endpoint and tests that inject in-memory repositories never touch a database.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Annotated, cast

from doktok_contracts.ports import (
    AuditLogRepository,
    CategoryRepository,
    DocumentRepository,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
    RagAnswerer,
    Retriever,
    StatsRepository,
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


_DB_LOCK = threading.Lock()


def _get_database(request: Request) -> Database:
    database: Database | None = getattr(request.app.state, "database", None)
    if database is not None:
        return database
    # Guard creation so concurrent first-requests don't each build a pool + run migrate twice
    # (double-checked: re-read state inside the lock).
    with _DB_LOCK:
        database = getattr(request.app.state, "database", None)
        if database is None:
            from doktok_storage_postgres import Database, migrate

            settings = request.app.state.settings
            # Size the pool to expected concurrency: sync routes each hold a connection during a
            # slow Ollama call, so the default (4) starves under a handful of concurrent requests.
            database = Database(settings.database_url, max_size=settings.api_db_pool_size)
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
        OllamaEmbeddingProvider(
            settings.embedding_model,
            settings.ollama_base_url,
            timeout=settings.rag_timeout_seconds,
        ),
    )
    registry.register(Retriever, retriever)
    return retriever


def get_rag_answerer(request: Request) -> RagAnswerer:
    registry = request.app.state.registry
    if registry.is_registered(RagAnswerer):
        return cast(RagAnswerer, registry.resolve(RagAnswerer))

    from doktok_core.rag.answerer import DefaultRagAnswerer
    from doktok_core.rag.reranker import LlmReranker
    from doktok_provider_ollama import OllamaChatModelProvider

    settings = request.app.state.settings
    chat_model = OllamaChatModelProvider(
        settings.default_model,
        settings.ollama_base_url,
        timeout=settings.rag_timeout_seconds,
        num_ctx=settings.chat_num_ctx,
        keep_alive=settings.chat_keep_alive,
    )
    # The listwise reranker emits only a short JSON array - cap its output (and allow a smaller,
    # swappable model) so it doesn't consume the answer call's full generation budget.
    rerank_model = OllamaChatModelProvider(
        settings.rerank_model or settings.default_model,
        settings.ollama_base_url,
        timeout=settings.rag_timeout_seconds,
        num_ctx=settings.chat_num_ctx,
        num_predict=settings.rerank_num_predict,
        keep_alive=settings.chat_keep_alive,
    )
    answerer = DefaultRagAnswerer(
        get_retriever(request),
        chat_model,
        reranker=LlmReranker(rerank_model),
        retrieve_k=settings.rag_retrieve_k,
        min_score=settings.rag_min_score,
    )
    registry.register(RagAnswerer, answerer)
    return answerer


def get_feature_repository(request: Request) -> FeatureRepository:
    registry = request.app.state.registry
    if registry.is_registered(FeatureRepository):
        return cast(FeatureRepository, registry.resolve(FeatureRepository))

    from doktok_storage_postgres import PostgresFeatureRepository

    repository = PostgresFeatureRepository(_get_database(request))
    registry.register(FeatureRepository, repository)
    return repository


def get_category_repository(request: Request) -> CategoryRepository:
    registry = request.app.state.registry
    if registry.is_registered(CategoryRepository):
        return cast(CategoryRepository, registry.resolve(CategoryRepository))

    from doktok_storage_postgres import PostgresCategoryRepository

    repository = PostgresCategoryRepository(_get_database(request))
    registry.register(CategoryRepository, repository)
    return repository


def get_stats_repository(request: Request) -> StatsRepository:
    registry = request.app.state.registry
    if registry.is_registered(StatsRepository):
        return cast(StatsRepository, registry.resolve(StatsRepository))

    from doktok_storage_postgres import PostgresStatsRepository

    repository = PostgresStatsRepository(_get_database(request))
    registry.register(StatsRepository, repository)
    return repository


Tenant = Annotated[TenantContext, Depends(require_tenant)]
