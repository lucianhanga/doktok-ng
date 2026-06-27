"""FastAPI dependencies: tenant authentication and lazy composition.

``require_tenant`` enforces bearer-token auth and resolves the caller's tenant (ADR-0008).
Repositories are resolved from the app's DI registry; if nothing is bound (production),
Postgres-backed repositories are created lazily on first use over a single shared database handle,
so the health endpoint and tests that inject in-memory repositories never touch a database.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Annotated, cast

from doktok_contracts.ports import (
    AppSettingsRepository,
    AuditLogRepository,
    CategoryRepository,
    ChatModelProvider,
    ChatThreadRepository,
    ChunkRepository,
    DocumentRepository,
    EmbeddingProjectionRepository,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
    ProjectionRequestRepository,
    RagAnswerer,
    RecordRepository,
    Retriever,
    StatsRepository,
)
from doktok_contracts.schemas import TenantContext
from doktok_core.security.auth import resolve_tenant
from doktok_core.security.egress import (
    EgressBlocked,
    openai_egress_allowed,
    purpose_requires_egress,
)
from fastapi import Depends, Header, HTTPException, Request, status

if TYPE_CHECKING:
    from doktok_core.visualizations.map_service import EmbeddingMapService
    from doktok_storage_postgres import Database

logger = logging.getLogger("doktok.api")

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
    from doktok_core.logging_setup import tenant_id_var

    tenant_id_var.set(tenant_id)  # correlate log lines by tenant (APP-12)
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
            # Headless bootstrap: seed the AI provider split from env on a fresh DB (APP-2).
            from doktok_core.settings.bootstrap import seed_ai_settings
            from doktok_storage_postgres import PostgresAppSettingsRepository

            seed_ai_settings(
                PostgresAppSettingsRepository(database, secrets_key=settings.secrets_key), settings
            )
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
    # Per-purpose Ollama URL override (M13 #369): embeddings can target a different Ollama host.
    embedding = get_app_settings_repository(request).get_ai_settings().embedding
    embedding_url = embedding.ollama_base_url or settings.ollama_base_url
    retriever = HybridPostgresRetriever(
        _get_database(request),
        OllamaEmbeddingProvider(
            settings.embedding_model,
            embedding_url,
            timeout=settings.rag_timeout_seconds,
            keep_alive=settings.embedding_keep_alive,
            num_ctx=settings.embedding_num_ctx,
        ),
    )
    registry.register(Retriever, retriever)
    return retriever


def _build_rag_chat_model(request: Request) -> ChatModelProvider:
    """The chat model for the RAG/interrogation purpose (Settings tab > AI), built per its provider.
    Shared by the RAG answerer and the chat aggregation router so both use the configured model."""
    from doktok_core.settings.catalog import ollama_think_for, openai_reasoning_effort

    settings = request.app.state.settings
    app_settings = get_app_settings_repository(request)
    rag = app_settings.get_ai_settings().rag
    # DB value (Settings UI) wins; fall back to the env key for headless/bootstrap deploys (APP-7).
    openai_key = app_settings.get_openai_api_key() or settings.openai_api_key
    use_openai = openai_egress_allowed(key=openai_key, no_egress=settings.no_egress)
    model_provider: ChatModelProvider
    # Defense-in-depth: if the RAG destination is off-host while no-egress is on (OpenAI, or a
    # remote Ollama URL - which the OpenAI-only check missed), refuse to build it. The chat call
    # then errors loudly instead of silently answering on a substituted model or egressing.
    if settings.no_egress and purpose_requires_egress(
        rag.provider, rag.ollama_base_url, default_url=settings.ollama_base_url
    ):
        logger.error("RAG destination is off-host but DOKTOK_NO_EGRESS is on; chat blocked")
        return EgressBlocked("Document interrogation")
    if rag.provider == "openai" and use_openai:
        from doktok_provider_openai import OpenAiChatModelProvider

        effort = openai_reasoning_effort(rag.reasoning, rag.model)
        model_provider = OpenAiChatModelProvider(
            rag.model, openai_key, timeout=settings.rag_timeout_seconds, reasoning_effort=effort
        )
    else:
        from doktok_provider_ollama import OllamaChatModelProvider

        if rag.provider == "openai":
            reason = (
                "DOKTOK_NO_EGRESS is true; refusing to egress"
                if openai_key and settings.no_egress
                else "no API key is configured"
            )
            logger.warning(
                "Document interrogation is set to OpenAI %s but %s; "
                "falling back to the local default model %s",
                rag.model,
                reason,
                settings.default_model,
            )
        model = rag.model if rag.provider == "ollama" else settings.default_model
        model_provider = OllamaChatModelProvider(
            model,
            rag.ollama_base_url or settings.ollama_base_url,  # per-purpose override (M13 #369)
            timeout=settings.rag_timeout_seconds,
            num_ctx=rag.num_ctx,
            keep_alive=settings.chat_keep_alive,
            think=ollama_think_for(rag.reasoning, model, structured=False),
        )
    return model_provider


def get_chat_model(request: Request) -> ChatModelProvider:
    registry = request.app.state.registry
    if registry.is_registered(ChatModelProvider):
        return cast(ChatModelProvider, registry.resolve(ChatModelProvider))
    model = _build_rag_chat_model(request)
    registry.register(ChatModelProvider, model)
    return model


def get_rag_answerer(request: Request) -> RagAnswerer:
    registry = request.app.state.registry
    if registry.is_registered(RagAnswerer):
        return cast(RagAnswerer, registry.resolve(RagAnswerer))

    from doktok_core.rag.answerer import DefaultRagAnswerer
    from doktok_core.rag.reranker import LlmReranker

    settings = request.app.state.settings
    # Effective RAG model selection (Settings tab > AI section), persisted; applied at startup.
    app_settings = get_app_settings_repository(request)
    rag = app_settings.get_ai_settings().rag
    openai_key = app_settings.get_openai_api_key() or settings.openai_api_key
    use_openai = openai_egress_allowed(key=openai_key, no_egress=settings.no_egress)
    chat_model = _build_rag_chat_model(request)
    rerank_model: ChatModelProvider
    if rag.provider == "openai" and use_openai:
        rerank_model = chat_model  # same remote model; the prompt already caps the rerank output
    else:
        from doktok_provider_ollama import OllamaChatModelProvider

        # The listwise reranker emits only a short JSON array - cap its output (and allow a smaller,
        # swappable model) so it doesn't consume the answer call's full generation budget.
        model = rag.model if rag.provider == "ollama" else settings.default_model
        rerank_model = OllamaChatModelProvider(
            settings.rerank_model or model,
            rag.ollama_base_url or settings.ollama_base_url,  # per-purpose override (M13 #369)
            timeout=settings.rag_timeout_seconds,
            num_ctx=rag.num_ctx,
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


def get_chunk_repository(request: Request) -> ChunkRepository:
    registry = request.app.state.registry
    if registry.is_registered(ChunkRepository):
        return cast(ChunkRepository, registry.resolve(ChunkRepository))

    from doktok_storage_postgres import PostgresChunkRepository

    repository = PostgresChunkRepository(_get_database(request))
    registry.register(ChunkRepository, repository)
    return repository


def get_chat_thread_repository(request: Request) -> ChatThreadRepository:
    registry = request.app.state.registry
    if registry.is_registered(ChatThreadRepository):
        return cast(ChatThreadRepository, registry.resolve(ChatThreadRepository))

    from doktok_storage_postgres import PostgresChatThreadRepository

    repository = PostgresChatThreadRepository(_get_database(request))
    registry.register(ChatThreadRepository, repository)
    return repository


def get_embedding_projection_repository(request: Request) -> EmbeddingProjectionRepository:
    registry = request.app.state.registry
    if registry.is_registered(EmbeddingProjectionRepository):
        return cast(EmbeddingProjectionRepository, registry.resolve(EmbeddingProjectionRepository))

    from doktok_storage_postgres import PostgresEmbeddingProjectionRepository

    repository = PostgresEmbeddingProjectionRepository(_get_database(request))
    registry.register(EmbeddingProjectionRepository, repository)
    return repository


def get_projection_request_repository(request: Request) -> ProjectionRequestRepository:
    registry = request.app.state.registry
    if registry.is_registered(ProjectionRequestRepository):
        return cast(ProjectionRequestRepository, registry.resolve(ProjectionRequestRepository))

    from doktok_storage_postgres import PostgresProjectionRequestRepository

    repository = PostgresProjectionRequestRepository(_get_database(request))
    registry.register(ProjectionRequestRepository, repository)
    return repository


def get_embedding_map_service(request: Request) -> EmbeddingMapService:
    from doktok_core.visualizations.map_service import EmbeddingMapService

    settings = request.app.state.settings
    return EmbeddingMapService(
        get_embedding_projection_repository(request),
        get_chunk_repository(request),
        get_category_repository(request),
        get_projection_request_repository(request),
        algorithm=settings.projection_algorithm,
        version=settings.projection_version,
    )


def get_app_settings_repository(request: Request) -> AppSettingsRepository:
    registry = request.app.state.registry
    if registry.is_registered(AppSettingsRepository):
        return cast(AppSettingsRepository, registry.resolve(AppSettingsRepository))

    from doktok_storage_postgres import PostgresAppSettingsRepository

    settings = request.app.state.settings
    repository = PostgresAppSettingsRepository(
        _get_database(request),
        secrets_key=settings.secrets_key,
        backup_status_dir=f"{settings.backup_dir.rstrip('/')}/status",
    )
    registry.register(AppSettingsRepository, repository)
    return repository


def get_record_repository(request: Request) -> RecordRepository:
    registry = request.app.state.registry
    if registry.is_registered(RecordRepository):
        return cast(RecordRepository, registry.resolve(RecordRepository))

    from doktok_storage_postgres import PostgresRecordRepository

    repository = PostgresRecordRepository(_get_database(request))
    registry.register(RecordRepository, repository)
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
