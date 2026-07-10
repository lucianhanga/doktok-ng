"""FastAPI dependencies: tenant authentication and lazy composition.

``require_tenant`` enforces bearer-token auth and resolves the caller's tenant (ADR-0008).
Repositories are resolved from the app's DI registry; if nothing is bound (production),
Postgres-backed repositories are created lazily on first use over a single shared database handle,
so the health endpoint and tests that inject in-memory repositories never touch a database.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
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
    EmbeddingProvider,
    EntityMergeAdjudicator,
    EntityRepository,
    FeatureRepository,
    IngestionJobRepository,
    KnowledgeGraphRepository,
    MemoryRepository,
    ProjectionRequestRepository,
    RagAnswerer,
    RecordRepository,
    Reranker,
    Retriever,
    StatsRepository,
    TenantRegistry,
)
from doktok_contracts.schemas import TenantContext
from doktok_core.security.auth import resolve_credential
from doktok_core.security.egress import (
    EgressBlocked,
    effective_no_egress,
    openai_egress_allowed,
    purpose_requires_egress,
)
from doktok_core.security.roles import Role, parse_role, role_at_least
from fastapi import Depends, Header, HTTPException, Request, status

if TYPE_CHECKING:
    from doktok_core.tools import ToolRegistry
    from doktok_core.visualizations.map_service import EmbeddingMapService
    from doktok_storage_postgres import Database

logger = logging.getLogger("doktok.api")

_BEARER_PREFIX = "Bearer "


def require_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """Authenticate the request and return its tenant. Fail-closed if no auth is configured.

    Resolution tries the DB-backed registry first (hashed ``api_tokens`` lookup, #554) and falls
    back to the static ``DOKTOK_TENANT_TOKENS`` map (ADR-0008). The registry is opt-in: only used
    when one has been registered (e.g. a DB-backed deployment), so static-only deployments keep
    their exact prior behavior.
    """
    tokens = request.app.state.settings.tenant_tokens
    registry = _maybe_tenant_registry(request)
    if not tokens and registry is None:
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
    resolution = resolve_credential(
        presented,
        registry=registry,
        static_tokens=tokens,
        jwt_secret=effective_jwt_secret(request.app.state.settings),
    )
    if resolution is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Deactivation enforcement (#557): a credential that resolves to a specific user is only valid
    # while that user is active. This is the authoritative revocation lever - it blocks a
    # deactivated (or not-yet-accepted 'invited') user's session JWT AND api-tokens immediately,
    # regardless of token TTL. Tenant-scoped tokens (no user) and registry-less deployments skip it.
    if resolution.user_id is not None and registry is not None:
        user = registry.get_user(resolution.tenant_id, resolution.user_id)
        if user is None or user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="user is not active",
                headers={"WWW-Authenticate": "Bearer"},
            )
    from doktok_core.logging_setup import tenant_id_var

    tenant_id_var.set(resolution.tenant_id)  # correlate log lines by tenant (APP-12)
    return TenantContext(tenant_id=resolution.tenant_id, user_id=resolution.user_id)


def effective_jwt_secret(settings: object) -> str:
    """The secret used to sign/verify login session JWTs (#555): the dedicated
    ``DOKTOK_AUTH_JWT_SECRET`` if set, else ``DOKTOK_SECRETS_KEY``, else empty (login disabled)."""
    return getattr(settings, "auth_jwt_secret", "") or getattr(settings, "secrets_key", "")


def require_user(tenant: Annotated[TenantContext, Depends(require_tenant)]) -> TenantContext:
    """Like :func:`require_tenant`, but requires an authenticated *user* identity (#555).

    A user-scoped session JWT or a user-bound API token carries ``user_id``; a tenant-scoped static
    token (``DOKTOK_TENANT_TOKENS``) does not. Routes that must attribute an action to a person
    (per-user preferences #558, audit actor #560) depend on this and reject the tenant-only case
    with 403 rather than silently acting without a user.
    """
    if tenant.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user authentication required (log in via /api/v1/auth/login)",
        )
    return tenant


def _maybe_tenant_registry(request: Request) -> TenantRegistry | None:
    """The registered ``TenantRegistry``, or ``None`` if none is wired (static-only deployment).

    Deliberately does NOT build one on demand: the auth path must not require a database for
    static-token deployments. A DB-backed deployment registers the registry at startup (or via
    :func:`get_tenant_registry`) to activate the ``api_tokens`` resolution path.
    """
    registry = request.app.state.registry
    if registry.is_registered(TenantRegistry):
        return cast(TenantRegistry, registry.resolve(TenantRegistry))
    return None


def get_tenant_registry(request: Request) -> TenantRegistry:
    """DB-backed ``TenantRegistry`` (lazy build + register) for admin/auth routes (#554)."""
    registry = request.app.state.registry
    if registry.is_registered(TenantRegistry):
        return cast(TenantRegistry, registry.resolve(TenantRegistry))

    from doktok_storage_postgres import PostgresTenantRegistry

    tenant_registry = PostgresTenantRegistry(_get_database(request))
    registry.register(TenantRegistry, tenant_registry)
    return tenant_registry


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


def get_knowledge_graph_repository(request: Request) -> KnowledgeGraphRepository:
    registry = request.app.state.registry
    if registry.is_registered(KnowledgeGraphRepository):
        return cast(KnowledgeGraphRepository, registry.resolve(KnowledgeGraphRepository))

    from doktok_storage_postgres import PostgresKnowledgeGraphRepository

    repository = PostgresKnowledgeGraphRepository(_get_database(request))
    registry.register(KnowledgeGraphRepository, repository)
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
    no_egress = effective_no_egress(
        app_settings.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    use_openai = openai_egress_allowed(key=openai_key, no_egress=no_egress)
    model_provider: ChatModelProvider
    # Defense-in-depth: if the RAG destination is off-host while no-egress is on (OpenAI, or a
    # remote Ollama URL - which the OpenAI-only check missed), refuse to build it. The chat call
    # then errors loudly instead of silently answering on a substituted model or egressing.
    if no_egress and purpose_requires_egress(
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
                "no-egress is on; refusing to egress"
                if openai_key and no_egress
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
    ai_settings = app_settings.get_ai_settings()
    rag = ai_settings.rag
    openai_key = app_settings.get_openai_api_key() or settings.openai_api_key
    no_egress = effective_no_egress(
        app_settings.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    use_openai = openai_egress_allowed(key=openai_key, no_egress=no_egress)
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
    # KAG Phase 3 (additive): a deterministic graph retriever fuses an entity-neighborhood / path
    # subgraph into retrieval on relational questions; non-relational turns are unaffected.
    from doktok_core.knowledge_graph.retrieval import DefaultGraphRetriever

    graph_retriever = DefaultGraphRetriever(
        get_knowledge_graph_repository(request),
        documents=get_document_repository(request),
    )
    # Reranker (#466): a dedicated on-host Qwen3-Reranker when selected + available, else the LLM
    # listwise reranker. The native path degrades to the LLM one when torch/the model isn't
    # installed, so reranking never hard-fails.
    reranker: Reranker
    if ai_settings.rerank.provider in ("qwen-reranker", "qwen_reranker"):
        try:
            from doktok_provider_reranker import QwenReranker

            reranker = QwenReranker(ai_settings.rerank.model)
        except Exception:  # noqa: BLE001 - missing torch/model must fall back, never crash startup
            logging.getLogger("doktok.rag.rerank").warning(
                "local reranker %s unavailable; using the LLM listwise reranker",
                ai_settings.rerank.model,
                exc_info=True,
            )
            reranker = LlmReranker(rerank_model)
    else:
        reranker = LlmReranker(rerank_model)
    answerer = DefaultRagAnswerer(
        get_retriever(request),
        chat_model,
        reranker=reranker,
        retrieve_k=settings.rag_retrieve_k,
        min_score=settings.rag_min_score,
        rerank_min_relevance=settings.rerank_min_relevance,
        graph_retriever=graph_retriever,
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


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    registry = request.app.state.registry
    if registry.is_registered(EmbeddingProvider):
        return cast(EmbeddingProvider, registry.resolve(EmbeddingProvider))

    from doktok_provider_ollama import OllamaEmbeddingProvider

    settings = request.app.state.settings
    embedding = get_app_settings_repository(request).get_ai_settings().embedding
    provider = OllamaEmbeddingProvider(
        settings.embedding_model,
        embedding.ollama_base_url or settings.ollama_base_url,
        timeout=settings.rag_timeout_seconds,
        keep_alive=settings.embedding_keep_alive,
        num_ctx=settings.embedding_num_ctx,
    )
    registry.register(EmbeddingProvider, provider)
    return provider


def get_memory_repository(request: Request) -> MemoryRepository:
    registry = request.app.state.registry
    if registry.is_registered(MemoryRepository):
        return cast(MemoryRepository, registry.resolve(MemoryRepository))

    from doktok_storage_postgres import PostgresMemoryRepository

    repository = PostgresMemoryRepository(_get_database(request))
    registry.register(MemoryRepository, repository)
    return repository


def get_tool_registry(request: Request) -> ToolRegistry:
    """The agentic-chat tool set (ADR-0022 Phase 2b), wired to this tenant's repositories. Rebuilt
    per request (cheap) so it always reflects the current providers/settings."""
    from doktok_core.knowledge_graph.retrieval import DefaultGraphRetriever
    from doktok_core.tools.library import build_default_registry

    graph_retriever = DefaultGraphRetriever(
        get_knowledge_graph_repository(request),
        documents=get_document_repository(request),
    )
    return build_default_registry(
        documents=get_document_repository(request),
        entities=get_entity_repository(request),
        retriever=get_retriever(request),
        records=get_record_repository(request),
        graph_retriever=graph_retriever,
        stats=get_stats_repository(request),
        categories=get_category_repository(request),
    )


def get_entity_merge_adjudicator(request: Request) -> EntityMergeAdjudicator | None:
    """Resolve the pipeline-model adjudicator for entity merge suggestions (#510).

    Returns None (graceful fallback) when:
    - the adjudicator is already cached as None (egress blocked, test mode without a DB),
    - app settings are unavailable (test mode, no database configured),
    - the pipeline destination is off-host while no-egress is on.

    The caller (``list_merge_suggestions``) falls back to deterministic suggestions when None.
    """
    registry = request.app.state.registry
    if registry.is_registered(EntityMergeAdjudicator):
        return cast(EntityMergeAdjudicator, registry.resolve(EntityMergeAdjudicator))

    try:
        adjudicator = _build_entity_merge_adjudicator(request)
    except Exception:
        logger.warning(
            "could not build entity merge adjudicator; merge-suggestions will be deterministic",
            exc_info=True,
        )
        return None

    if adjudicator is not None:
        registry.register(EntityMergeAdjudicator, adjudicator)
    return adjudicator


def _build_entity_merge_adjudicator(request: Request) -> EntityMergeAdjudicator | None:
    """Build the adjudicator from the configured pipeline model (no new model required)."""
    from doktok_core.settings.catalog import ollama_think_for, openai_reasoning_effort

    settings = request.app.state.settings
    app_settings = get_app_settings_repository(request)
    ai = app_settings.get_ai_settings()
    pl = ai.pipeline
    pl_url: str = pl.ollama_base_url or settings.ollama_base_url
    openai_key: str = app_settings.get_openai_api_key() or settings.openai_api_key
    no_egress = effective_no_egress(
        app_settings.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    pipeline_egress_blocked = no_egress and purpose_requires_egress(
        pl.provider, pl.ollama_base_url, default_url=settings.ollama_base_url
    )
    if pipeline_egress_blocked:
        logger.warning(
            "entity merge adjudicator: pipeline destination is off-host while no-egress is on; "
            "merge-suggestions will be deterministic"
        )
        return None

    use_openai = pl.provider == "openai" and openai_egress_allowed(
        key=openai_key, no_egress=no_egress
    )
    timeout: float = settings.ollama_timeout_seconds

    if use_openai:
        from doktok_provider_openai.adjudicator import OpenAiEntityMergeAdjudicator

        # Adjudication is a fast yes/no JSON call - force reasoning OFF regardless of the pipeline
        # density (no reasoning tokens: requirement + cost). The annotation also gives mypy a
        # concrete return type when `providers` isn't in its scope (CI).
        openai_adj: EntityMergeAdjudicator = OpenAiEntityMergeAdjudicator(
            pl.model,
            openai_key,
            timeout=timeout,
            reasoning_effort=openai_reasoning_effort("off", pl.model),
        )
        return openai_adj
    else:
        from doktok_provider_ollama.adjudicator import OllamaEntityMergeAdjudicator

        p_model = pl.model if pl.provider == "ollama" else settings.default_model
        p_ctx = pl.num_ctx if pl.provider == "ollama" else settings.enrich_num_ctx
        # Force thinking off ("off"), not pl.reasoning, so adjudication never reasons - except a MoE
        # model that cannot disable thinking with structured output (arch limitation).
        p_think = ollama_think_for("off", p_model, structured=True)
        ollama_adj: EntityMergeAdjudicator = OllamaEntityMergeAdjudicator(
            p_model,
            p_model,
            pl_url,
            timeout=timeout,
            num_ctx=p_ctx,
            think=p_think,
            keep_alive=settings.enrich_keep_alive,
        )
        return ollama_adj


Tenant = Annotated[TenantContext, Depends(require_tenant)]
AuthenticatedUser = Annotated[TenantContext, Depends(require_user)]


def resolve_caller_role(request: Request, tenant: TenantContext) -> Role:
    """The RBAC role of the authenticated caller (#556).

    A tenant-scoped credential with no user identity (static ``DOKTOK_TENANT_TOKENS`` / a
    user-less api_token) is the local-first single operator: it resolves to ``admin`` so existing
    single-tenant deployments keep full access with no configuration. A user-scoped caller's role
    comes from the registry (authoritative + revocable); if it cannot be resolved, we fail closed to
    ``viewer`` (least privilege).
    """
    if tenant.user_id is None:
        return Role.ADMIN
    registry = _maybe_tenant_registry(request)
    if registry is None:
        return Role.VIEWER
    user = registry.get_user(tenant.tenant_id, tenant.user_id)
    return parse_role(user.role) if user else Role.VIEWER


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def make_write_guard(minimum: Role) -> Callable[[Request, TenantContext], None]:
    """A router-level dependency that requires ``minimum`` role for unsafe (write) methods (#556).

    Safe methods (GET/HEAD/OPTIONS) pass for any authenticated caller - every authenticated caller
    is at least a viewer - so read endpoints are unaffected. Unsafe methods (POST/PUT/PATCH/DELETE)
    are rejected with 403 unless the caller's role meets ``minimum``. Applied at ``include_router``
    so it gates every write in a router without touching individual handlers.
    """

    def _guard(request: Request, tenant: Tenant) -> None:
        if request.method in _SAFE_METHODS:
            return
        role = resolve_caller_role(request, tenant)
        if not role_at_least(role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this action requires the '{minimum.value}' role",
            )

    return _guard


def require_admin(request: Request, tenant: Tenant) -> TenantContext:
    """Require an admin caller for ANY method (#559). Unlike :func:`make_write_guard`, this also
    gates reads - administration endpoints (member/token listings) must not be readable by
    non-admins. Applied at the admin router's include so it covers every route there."""
    if not role_at_least(resolve_caller_role(request, tenant), Role.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="administrator role required",
        )
    return tenant


AdminUser = Annotated[TenantContext, Depends(require_admin)]
