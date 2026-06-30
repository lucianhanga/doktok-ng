"""Worker composition root: wire ports to adapters, one bundle per tenant (ADR-0001, ADR-0007)."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace

from doktok_contracts.ports import (
    CategoryClassifier,
    ChatModelProvider,
    EmbeddingProvider,
    EntityNerExtractor,
    FeatureProcessor,
    MetadataExtractor,
    OcrExtractor,
    RecordExtractor,
    RelationExtractor,
)
from doktok_contracts.schemas import AiPurposeSettings
from doktok_core.config import Settings
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.extraction.service import ExtractionResult, extract_document
from doktok_core.features.processors import (
    ChunkEmbedFeature,
    DocClassifyFeature,
    DocMetadataFeature,
    EntitiesFeature,
    EntityGraphFeature,
    NerFeature,
    RelationExtractFeature,
    StructuredRecordsFeature,
    ThumbnailFeature,
)
from doktok_core.features.reconciler import FeatureReconciler
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.extract_stage import ExtractStage
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.knowledge_graph.alias import resolve_tenant_aliases
from doktok_core.security.egress import (
    EgressBlocked,
    effective_no_egress,
    openai_egress_allowed,
    purpose_requires_egress,
    url_requires_egress,
)
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_core.settings.bootstrap import seed_ai_settings
from doktok_core.settings.catalog import ollama_think_for, openai_reasoning_effort
from doktok_core.visualizations.service import ProjectionRunner, ProjectionService
from doktok_modalities_files import (
    DirectTextExtractor,
    GotenbergNormalizer,
    LibmagicMimeDetector,
    PyMuPdfClassifier,
    PyMuPdfRenderer,
    PyMuPdfTextExtractor,
    PyMuPdfThumbnailer,
    SearchablePdfBuilder,
)
from doktok_provider_ollama import (
    OllamaCategoryClassifier,
    OllamaChatModelProvider,
    OllamaEmbeddingProvider,
    OllamaEntityNerExtractor,
    OllamaMetadataExtractor,
    OllamaRecordExtractor,
    OllamaRelationExtractor,
    OllamaVisionOcr,
)
from doktok_provider_openai import (
    OpenAiCategoryClassifier,
    OpenAiChatModelProvider,
    OpenAiEntityNerExtractor,
    OpenAiMetadataExtractor,
    OpenAiRecordExtractor,
    OpenAiRelationExtractor,
)
from doktok_provider_paddleocr import PaddleOcr
from doktok_provider_projection import SklearnEmbeddingProjector
from doktok_provider_rapidocr import RapidOcr
from doktok_storage_filesystem import (
    LocalFileStorage,
    QuarantineService,
    Sha256HashService,
)
from doktok_storage_postgres import (
    Database,
    PostgresAppSettingsRepository,
    PostgresAuditLogRepository,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEmbeddingProjectionRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresKnowledgeGraphRepository,
    PostgresLexicalTermExtractor,
    PostgresProjectionRequestRepository,
    PostgresRecordRepository,
    migrate,
)

logger = logging.getLogger("doktok.worker")


def _torch_device() -> str | None:
    """Torch device for the local span models (``DOKTOK_NER_DEVICE``, e.g. cpu|cuda). None = cpu."""
    return os.environ.get("DOKTOK_NER_DEVICE") or None


def _resolve_ner_backend(
    cfg: AiPurposeSettings,
    fallback: EntityNerExtractor,
    *,
    key: str,
    no_egress: bool,
    default_url: str,
    timeout: float,
    keep_alive: str,
) -> tuple[EntityNerExtractor, str]:
    """Build the NER extractor for the configured ``ner`` purpose (ADR-0023): a local span model
    (``gliner`` / ``nuner``, no egress) or an LLM (``openai`` / ``ollama``). A local backend that
    fails to load (runtime/model missing) falls back to ``fallback`` (the pipeline LLM NER) so the
    worker never crashes. Returns ``(extractor, signature-token)`` for the rebuild signature.
    """
    provider, model = cfg.provider, cfg.model
    if provider in ("gliner", "nuner"):
        try:
            from doktok_provider_gliner import GlinerEntityNerExtractor, NuNerEntityNerExtractor

            device = _torch_device()
            ext: EntityNerExtractor = (
                GlinerEntityNerExtractor(model, device=device)
                if provider == "gliner"
                else NuNerEntityNerExtractor(model, device=device)
            )
            logger.debug("NER backend: local %s (%s)", provider, model)
            return ext, f"{provider}:{model}:{device or 'cpu'}"
        except Exception as exc:  # noqa: BLE001 - a load failure must fall back, never crash
            logger.warning(
                "NER set to local %s (%s) but unavailable (%s); falling back to the pipeline LLM",
                provider,
                model,
                exc,
            )
            return fallback, f"{provider}-fallback"
    if provider == "openai":
        if no_egress or not key:
            logger.error(
                "NER is set to OpenAI but %s; NER blocked",
                "no-egress is on" if no_egress else "the API key is missing",
            )
            return EgressBlocked("NER"), "openai:blocked"
        effort = openai_reasoning_effort(cfg.reasoning, model)
        return OpenAiEntityNerExtractor(
            model, key, timeout=timeout, reasoning_effort=effort
        ), f"openai:{model}"
    # ollama (not offered in the catalog, but honored if hand-configured)
    if no_egress and url_requires_egress(cfg.ollama_base_url, default_url=default_url):
        return EgressBlocked("NER"), "ollama:blocked"
    base = cfg.ollama_base_url or default_url
    return OllamaEntityNerExtractor(
        model,
        model,
        base,
        timeout=timeout,
        num_ctx=cfg.num_ctx,
        think=ollama_think_for(cfg.reasoning, model, structured=True),
        keep_alive=keep_alive,
    ), f"ollama:{model}:{base}"


def _resolve_relation_backend(
    cfg: AiPurposeSettings,
    fallback: RelationExtractor,
    *,
    key: str,
    no_egress: bool,
    default_url: str,
    timeout: float,
    keep_alive: str,
) -> tuple[RelationExtractor, str]:
    """Build the relation (KAG) extractor for the configured ``keg`` purpose (ADR-0023): local
    GLiNER-Relex (no egress) or an LLM. A local backend that fails to load uses ``fallback``
    (the pipeline LLM relation extractor). Returns ``(extractor, signature-token)``.
    """
    provider, model = cfg.provider, cfg.model
    if provider in ("gliner-relex", "gliner_relex"):
        try:
            from doktok_provider_gliner import GlinerRelexRelationExtractor

            device = _torch_device()
            ext = GlinerRelexRelationExtractor(model, device=device)
            logger.debug("relation backend: local gliner-relex (%s)", model)
            return ext, f"gliner-relex:{model}:{device or 'cpu'}"
        except Exception as exc:  # noqa: BLE001 - a load failure must fall back, never crash
            logger.warning(
                "Relations local gliner-relex (%s) unavailable (%s); falling back to LLM",
                model,
                exc,
            )
            return fallback, "gliner-relex-fallback"
    if provider == "openai":
        if no_egress or not key:
            logger.error(
                "Relations set to OpenAI but %s; relations blocked",
                "no-egress is on" if no_egress else "the API key is missing",
            )
            return EgressBlocked("Relations"), "openai:blocked"
        effort = openai_reasoning_effort(cfg.reasoning, model)
        return OpenAiRelationExtractor(
            model, key, timeout=timeout, reasoning_effort=effort
        ), f"openai:{model}"
    if no_egress and url_requires_egress(cfg.ollama_base_url, default_url=default_url):
        return EgressBlocked("Relations"), "ollama:blocked"
    base = cfg.ollama_base_url or default_url
    return OllamaRelationExtractor(
        model,
        model,
        base,
        timeout=timeout,
        num_ctx=cfg.num_ctx,
        think=ollama_think_for(cfg.reasoning, model, structured=True),
        keep_alive=keep_alive,
    ), f"ollama:{model}:{base}"


def tenant_ids(settings: Settings) -> list[str]:
    """Unique tenant ids the worker should watch, derived from the token map."""
    seen: dict[str, None] = {}
    for tenant in settings.tenant_tokens.values():
        seen.setdefault(tenant, None)
    return list(seen)


@dataclass
class _AiClients:
    """The AI-settings-derived clients used by enrichment + the OCR judge (M13 #371). Rebuilt on a
    live settings change; ``signature`` is the change-detection key."""

    embedding: EmbeddingProvider
    judge: ChatModelProvider
    metadata: MetadataExtractor
    category: CategoryClassifier
    record: RecordExtractor
    ner: EntityNerExtractor
    relation: RelationExtractor
    signature: tuple[object, ...]
    # Human-readable summary of the active pipeline (provider/model/egress), logged at startup and
    # on a live change - NOT on every rebuild, so the worker log stays quiet between settings edits.
    description: str


def build_services(
    settings: Settings,
) -> tuple[
    list[IngestionServices],
    FeatureReconciler,
    ProjectionRunner,
    Database,
    Callable[[], None] | None,
    Callable[[], None],
    Callable[[], None],
    Callable[[], None],
    Callable[[], None],
    Callable[[], bool],
]:
    """Build per-tenant ingestion services, the feature reconciler, and a shared database handle.

    Ensures each tenant's lifecycle folders exist and runs migrations once.
    """
    # Size the pool for the parallel streams: up to `ingest_concurrency` ingestion workers +
    # `reconcile_concurrency` reconciler workers, each holding a connection only briefly.
    db = Database(
        settings.database_url,
        # Size for the widest reconcile fan-out we might choose below (OpenAI pipeline raises it),
        # so the pool never starves regardless of provider. Idle connections cost little.
        max_size=max(
            6,
            settings.ingest_concurrency
            + max(settings.reconcile_concurrency, settings.openai_reconcile_concurrency)
            + 2,
        ),
    )
    migrate(db)

    # Effective AI model selection (Settings tab > AI section), persisted; applied at startup.
    app_settings = PostgresAppSettingsRepository(db, secrets_key=settings.secrets_key)
    # Headless bootstrap: seed the provider split from env on a fresh DB (APP-2; no-op if saved).
    seed_ai_settings(app_settings, settings)
    heartbeat = app_settings.set_worker_heartbeat  # liveness signal for the backend probe (APP-5)
    is_quiesced = app_settings.get_maintenance_mode  # quiesce gate read each loop (APP-C3)
    pipeline = app_settings.get_ai_settings().pipeline
    # DB value (Settings UI) wins; fall back to the env key for headless/bootstrap deploys (APP-7).
    # (These startup values drive the reconciler concurrency + the warning below; the enrichment
    # clients themselves are (re)built from the *current* settings in build_ai_clients, M13 #371.)
    openai_key = app_settings.get_openai_api_key() or settings.openai_api_key
    # OCR parallelism comes from the Settings DB (live-reloaded by the worker), env is the fallback
    # default. This is the number of OCR worker processes directly - if more documents ingest at
    # once than this, their pages just share the pool (the process pool queues the extra work).
    _ocr_settings = app_settings.get_ocr_settings()
    ocr_concurrency = _ocr_settings.ocr_concurrency
    # OCR engine: the Settings DB value wins, env (DOKTOK_OCR_ENGINE) is the fallback (M17 #375).
    # An engine change applies on the next worker restart (the pool/extractor is built once here).
    ocr_engine = _ocr_settings.engine or settings.ocr_engine
    # Effective no-egress posture at startup (the in-app toggle / env default / host lock). The
    # live build_ai_clients re-resolves it per reconcile; this startup copy gates the one-time path
    # selection below, so a toggle change to it applies on the next worker restart.
    no_egress_startup = effective_no_egress(
        app_settings.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    use_openai_pipeline = pipeline.provider == "openai" and openai_egress_allowed(
        key=openai_key, no_egress=no_egress_startup
    )
    if pipeline.provider == "openai" and not use_openai_pipeline:
        if openai_key and no_egress_startup:
            logger.warning(
                "pipeline is set to OpenAI but no-egress is on; refusing to egress document "
                "content - using Ollama defaults. Turn off no-egress in Settings > AI to enable it."
            )
        else:
            logger.warning(
                "pipeline set to OpenAI but no API key configured; using Ollama defaults"
            )

    job_repo = PostgresIngestionJobRepository(db)
    document_repo = PostgresDocumentRepository(db)
    audit_log = PostgresAuditLogRepository(db)
    file_storage = LocalFileStorage()
    hash_service = Sha256HashService()
    mime_detector = LibmagicMimeDetector()
    security_policy = DefaultSecurityPolicy(max_file_mb=settings.max_file_mb)
    text_extractor = DirectTextExtractor()
    document_normalizer = GotenbergNormalizer(settings.gotenberg_url)
    pdf_extractor = PyMuPdfTextExtractor()
    timeout = settings.ollama_timeout_seconds
    ocr_extractor: OcrExtractor
    if ocr_engine == "paddleocr":
        # `ocr_concurrency` independent predictors = the number of pages OCR'd in parallel across
        # the whole worker (PaddleOCR is CPU-bound, ~1 core each). Set directly by the OCR setting.
        ocr_extractor = PaddleOcr(
            lang=settings.ocr_lang,
            pool_size=ocr_concurrency,
            cpu_threads=settings.ocr_cpu_threads,
            enable_mkldnn=settings.ocr_enable_mkldnn,
        )
    elif ocr_engine == "rapidocr":
        # Same PP-OCR models via ONNXRuntime (OpenVINO on Intel) - faster + lighter on weak CPUs and
        # immune to the Paddle oneDNN crash (M17 #375). Same process-pool model as PaddleOCR.
        ocr_extractor = RapidOcr(
            lang=settings.ocr_lang,
            pool_size=ocr_concurrency,
            cpu_threads=settings.ocr_cpu_threads,
            backend=settings.ocr_rapid_backend,
        )
    else:
        ocr_extractor = OllamaVisionOcr(
            settings.ocr_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=settings.ocr_num_ctx,
            num_predict=settings.ocr_num_predict,
            keep_alive=settings.ocr_keep_alive,
        )
    # Enhanced re-OCR extractor (PaddleOCR only): heavier PP-OCRv6 medium models + the orientation/
    # unwarp/textline preprocessors. Used for files dropped in ingest.enhanced/. Lazy pool (the
    # models only load on first use), kept smaller since the medium models are heavier.
    enhanced_ocr: PaddleOcr | None = None
    if ocr_engine == "paddleocr":
        enhanced_ocr = PaddleOcr(
            lang=settings.ocr_lang,
            det_model=settings.ocr_enhanced_det_model,
            rec_model=settings.ocr_enhanced_rec_model,
            pool_size=max(1, ocr_concurrency // 2),
            cpu_threads=settings.ocr_cpu_threads,
            preprocess=True,
            orient_vote=True,  # reliable 4-way 90/180/270 orientation (slower, ~4x per page)
            enable_mkldnn=settings.ocr_enable_mkldnn,
        )
    # Live-reload OCR parallelism from Settings (M7.6): the worker calls this between ingest scans
    # (no OCR in flight) to resize the PaddleOCR pool without a restart. Paddle-only; the Ollama OCR
    # path has no predictor pool to resize.
    ocr_reload: Callable[[], None] | None = None
    if isinstance(ocr_extractor, PaddleOcr | RapidOcr):
        resizable = ocr_extractor

        def ocr_reload() -> None:  # noqa: F811 - single definition, guarded by the isinstance
            resizable.reconfigure(app_settings.get_ocr_settings().ocr_concurrency)

    # Graceful shutdown: tear down the OCR process pools so their model-laden workers do not leak as
    # orphans on every worker restart. No-op for the Ollama OCR path.
    def cleanup() -> None:
        if isinstance(ocr_extractor, PaddleOcr | RapidOcr):
            ocr_extractor.shutdown()
        if enhanced_ocr is not None:
            enhanced_ocr.shutdown()

    pdf_renderer = PyMuPdfRenderer()
    searchable_pdf_builder = SearchablePdfBuilder()
    thumbnailer = PyMuPdfThumbnailer()
    pdf_classifier = PyMuPdfClassifier()
    chunker = FixedWindowChunker()
    # AI-independent adapters: built once and shared across live AI reloads.
    chunk_repo = PostgresChunkRepository(db)
    entity_extractor = RegexEntityExtractor()
    entity_repo = PostgresEntityRepository(db)
    knowledge_graph_repo = PostgresKnowledgeGraphRepository(db)
    lexical_term_extractor = PostgresLexicalTermExtractor(db)
    feature_repo = PostgresFeatureRepository(db)
    category_repo = PostgresCategoryRepository(db)
    record_repo = PostgresRecordRepository(db)

    # The enrichment clients (embedding, judge, and the metadata/category/record/NER extractors) and
    # the processors that wrap them are rebuilt from the *current* AI settings on demand (M13 #371),
    # so a Settings change (model/provider/per-purpose Ollama URL) applies without a worker restart.
    # ``signature`` captures every field that affects a client; ai_reload() rebuilds only on change.
    def build_ai_clients() -> _AiClients:
        ai = app_settings.get_ai_settings()
        pl = ai.pipeline
        pl_url = pl.ollama_base_url or settings.ollama_base_url  # per-purpose override (M13 #369)
        emb_url = ai.embedding.ollama_base_url or settings.ollama_base_url
        key = app_settings.get_openai_api_key() or settings.openai_api_key
        # Effective no-egress posture: the in-app toggle (Settings > AI), or the env default, or
        # forced on by a host lock. Live-reloaded with the AI settings (it is in the signature).
        no_egress = effective_no_egress(
            app_settings.get_no_egress(),
            env_default=settings.no_egress,
            lock=settings.no_egress_lock,
        )
        use_openai = pl.provider == "openai" and openai_egress_allowed(key=key, no_egress=no_egress)
        # Defense-in-depth (the PUT boundary already rejects these, but no_egress can be flipped on
        # AFTER a remote config was saved): if a purpose's destination is off-host while no-egress
        # is on, refuse to build the egressing client. Fail loud - never silently substitute or
        # egress to a remote URL anyway. The reconciler marks the affected features FAILED with the
        # message, so it surfaces in the activity log instead of a cryptic Connection refused.
        default_url = settings.ollama_base_url
        pipeline_egress_blocked = no_egress and purpose_requires_egress(
            pl.provider, pl.ollama_base_url, default_url=default_url
        )
        embedding_egress_blocked = no_egress and url_requires_egress(
            ai.embedding.ollama_base_url, default_url=default_url
        )
        embedding: EmbeddingProvider
        if embedding_egress_blocked:
            logger.error("embedding URL is off-host but DOKTOK_NO_EGRESS is on; embedding blocked")
            embedding = EgressBlocked("Embedding")
        else:
            embedding = OllamaEmbeddingProvider(
                settings.embedding_model,
                emb_url,
                timeout=timeout,
                keep_alive=settings.embedding_keep_alive,
                num_ctx=settings.embedding_num_ctx,
            )
        # The OCR-quality judge runs inside the ingestion pipeline, so it follows the SAME Data
        # Pipeline provider+model as the extractors below - never a separate hardcoded model. A
        # local pipeline keeps one model resident; an OpenAI pipeline sends the judge's tiny A/B
        # prompt to OpenAI too. (No-egress fallback: the local default_model, like the extractors.)
        judge: ChatModelProvider
        metadata_extractor: MetadataExtractor
        category_classifier: CategoryClassifier
        record_extractor: RecordExtractor
        ner_extractor: EntityNerExtractor
        relation_extractor: RelationExtractor
        if pipeline_egress_blocked:
            logger.error(
                "Data pipeline destination is off-host but DOKTOK_NO_EGRESS is on; enrichment + "
                "the OCR judge are blocked until Settings > AI is fixed or egress is enabled"
            )
            blocked = EgressBlocked("Data pipeline")
            judge = blocked
            metadata_extractor = blocked
            category_classifier = blocked
            record_extractor = blocked
            ner_extractor = blocked
            relation_extractor = blocked
            description = "blocked (data-pipeline destination off-host while no-egress is on)"
        elif use_openai:
            description = f"OpenAI {pl.model} (egress per AI settings)"
            effort = openai_reasoning_effort(pl.reasoning, pl.model)
            judge = OpenAiChatModelProvider(pl.model, key, timeout=timeout, reasoning_effort=effort)
            metadata_extractor = OpenAiMetadataExtractor(
                pl.model, key, timeout=timeout, reasoning_effort=effort
            )
            category_classifier = OpenAiCategoryClassifier(
                pl.model, key, timeout=timeout, reasoning_effort=effort
            )
            record_extractor = OpenAiRecordExtractor(
                pl.model, key, timeout=timeout, reasoning_effort=effort
            )
            ner_extractor = OpenAiEntityNerExtractor(
                pl.model, key, timeout=timeout, reasoning_effort=effort
            )
            relation_extractor = OpenAiRelationExtractor(
                pl.model, key, timeout=timeout, reasoning_effort=effort
            )
        else:
            # The Data Pipeline UI model when local; the canonical local default_model only as the
            # fallback when the pipeline is set to OpenAI but egress is off (no UI model to run).
            p_model = pl.model if pl.provider == "ollama" else settings.default_model
            p_ctx = pl.num_ctx if pl.provider == "ollama" else settings.enrich_num_ctx
            p_think = ollama_think_for(pl.reasoning, p_model, structured=True)
            p_repair = p_model  # JSON-repair on the same model: keeps a single model resident
            description = f"local Ollama {p_model} at {pl_url}"
            judge = OllamaChatModelProvider(
                p_model,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                keep_alive=settings.enrich_keep_alive,
                # the judge replies a single char (A/B), not JSON, so it is not "structured"
                think=ollama_think_for(pl.reasoning, p_model, structured=False),
            )
            metadata_extractor = OllamaMetadataExtractor(
                p_model,
                p_repair,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                think=p_think,
                keep_alive=settings.enrich_keep_alive,
            )
            category_classifier = OllamaCategoryClassifier(
                p_model,
                p_repair,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                think=p_think,
                keep_alive=settings.enrich_keep_alive,
            )
            record_extractor = OllamaRecordExtractor(
                p_model,
                p_repair,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                think=p_think,
                keep_alive=settings.enrich_keep_alive,
            )
            ner_extractor = OllamaEntityNerExtractor(
                p_model,
                p_repair,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                think=p_think,
                keep_alive=settings.enrich_keep_alive,
            )
            relation_extractor = OllamaRelationExtractor(
                p_model,
                p_repair,
                pl_url,
                timeout=timeout,
                num_ctx=p_ctx,
                think=p_think,
                keep_alive=settings.enrich_keep_alive,
            )
        # NER + KAG relations are their own AI purposes (ADR-0023): built from ai.ner / ai.keg
        # (local span model or LLM), with the pipeline-LLM extractors built above as the fallback.
        ner_extractor, ner_token = _resolve_ner_backend(
            ai.ner,
            ner_extractor,
            key=key,
            no_egress=no_egress,
            default_url=default_url,
            timeout=timeout,
            keep_alive=settings.enrich_keep_alive,
        )
        relation_extractor, rel_token = _resolve_relation_backend(
            ai.keg,
            relation_extractor,
            key=key,
            no_egress=no_egress,
            default_url=default_url,
            timeout=timeout,
            keep_alive=settings.enrich_keep_alive,
        )
        signature: tuple[object, ...] = (
            pl.provider,
            pl.model,
            pl.num_ctx,
            pl.reasoning,
            pl.ollama_base_url,
            ai.embedding.ollama_base_url,
            bool(key),
            use_openai,
            no_egress,
            ner_token,
            rel_token,
        )
        return _AiClients(
            embedding=embedding,
            judge=judge,
            metadata=metadata_extractor,
            category=category_classifier,
            record=record_extractor,
            ner=ner_extractor,
            relation=relation_extractor,
            signature=signature,
            description=description,
        )

    def build_processors(clients: _AiClients) -> list[FeatureProcessor]:
        procs: list[FeatureProcessor] = [
            ChunkEmbedFeature(document_repo, file_storage, chunker, clients.embedding, chunk_repo),
            EntitiesFeature(
                document_repo,
                file_storage,
                entity_extractor,
                lexical_term_extractor,
                entity_repo,
                lexical_terms_limit=settings.lexical_terms_limit,
            ),
            NerFeature(document_repo, file_storage, clients.ner, entity_repo),
            EntityGraphFeature(entity_repo, knowledge_graph_repo),
            RelationExtractFeature(
                document_repo,
                file_storage,
                clients.relation,
                entity_repo,
                knowledge_graph_repo,
            ),
            DocMetadataFeature(document_repo, file_storage, clients.metadata),
            DocClassifyFeature(document_repo, file_storage, clients.category, category_repo),
            StructuredRecordsFeature(document_repo, file_storage, clients.record, record_repo),
            ThumbnailFeature(document_repo, file_storage, thumbnailer),
        ]

        # Staged ingestion (ADR-0015): the `extract` stage runs OCR/extraction + activation in the
        # reconciler. The judge model it uses is rebuilt here too, so it tracks AI settings.
        def _extract(mime: str, path: str) -> tuple[ExtractionResult, bytes | None]:
            return extract_document(
                mime,
                path,
                text_extractor=text_extractor,
                pdf_extractor=pdf_extractor,
                ocr=ocr_extractor,
                renderer=pdf_renderer,
                builder=searchable_pdf_builder,
                classifier=pdf_classifier,
                ocr_image_coverage=settings.ocr_image_coverage,
                ocr_min_text_quality=settings.ocr_min_text_quality,
                chat_model=clients.judge,
                max_pages=settings.max_pages,
                ocr_concurrency=ocr_concurrency,
                ocr_dpi=settings.ocr_dpi,
                normalizer=document_normalizer,
            )

        if settings.staged_ingestion:
            procs.insert(
                0, ExtractStage(document_repo, file_storage, settings.files_root, _extract)
            )
        return procs

    ai_clients = build_ai_clients()
    processors = build_processors(ai_clients)
    ai_signature = ai_clients.signature
    logger.info("pipeline extraction: %s", ai_clients.description)
    stage_ledger = [(p.name, p.version) for p in processors]

    # Fan the reconciler wider when the pipeline is remote (OpenAI): its enrichment features are
    # network-bound and the API parallelizes well, whereas the local Ollama path thrashes a single
    # GPU. (Concurrency is set at startup; a live provider switch keeps this value - see ai_reload.)
    reconcile_concurrency = (
        settings.openai_reconcile_concurrency
        if use_openai_pipeline
        else settings.reconcile_concurrency
    )
    if use_openai_pipeline:
        logger.info(
            "pipeline on OpenAI: reconciler fans out to %d in parallel", reconcile_concurrency
        )
    reconciler = FeatureReconciler(
        feature_repo,
        processors,
        tenant_ids(settings),
        concurrency=reconcile_concurrency,
        audit_log=audit_log,
    )

    # Live AI-settings reload (M13 #371): between reconcile passes (no feature in flight), rebuild
    # the enrichment clients if the AI settings changed, so model/provider/Ollama-URL edits apply
    # without a worker restart. Cheap when unchanged (only a settings read + a signature compare).
    def ai_reload() -> None:
        nonlocal ai_signature
        clients = build_ai_clients()
        if clients.signature != ai_signature:
            reconciler.set_processors(build_processors(clients))
            ai_signature = clients.signature
            logger.info(
                "AI settings changed; rebuilt enrichment clients live (no restart) - "
                "pipeline extraction: %s",
                clients.description,
            )

    # KAG alias folding: a tenant-level, cross-document maintenance pass run by the worker after the
    # per-document features drain. It folds entity surface variants into one canonical node and is
    # idempotent, so re-running is a no-op once the graph is stable.
    def post_reconcile() -> None:
        for tenant_id in tenant_ids(settings):
            resolve_tenant_aliases(knowledge_graph_repo, tenant_id)

    # Insights embedding map (ADR-0016, M7.1): a tenant-aggregate projection job, drained from the
    # recompute queue. The reducer reuses the chunk repo's embeddings; projections are cached.
    projection_runner = ProjectionRunner(
        PostgresProjectionRequestRepository(db),
        ProjectionService(
            chunk_repo,
            SklearnEmbeddingProjector(
                algorithm=settings.projection_algorithm,
                n_neighbors=settings.projection_n_neighbors,
                min_cluster_size=settings.projection_min_cluster_size,
                pca_components=settings.projection_pca_components,
            ),
            PostgresEmbeddingProjectionRepository(db),
            algorithm=settings.projection_algorithm,
            version=settings.projection_version,
            max_points=settings.projection_max_points,
        ),
    )

    services: list[IngestionServices] = []
    for tenant_id in tenant_ids(settings):
        layout = FilesystemLayout(settings.files_root, tenant_id)
        layout.ensure()
        std = IngestionServices(
            tenant_id=tenant_id,
            job_repo=job_repo,
            document_repo=document_repo,
            file_storage=file_storage,
            hash_service=hash_service,
            mime_detector=mime_detector,
            security_policy=security_policy,
            quarantine_service=QuarantineService(layout),
            text_extractor=text_extractor,
            pdf_extractor=pdf_extractor,
            layout=layout,
            document_normalizer=document_normalizer,
            ocr_extractor=ocr_extractor,
            pdf_renderer=pdf_renderer,
            searchable_pdf_builder=searchable_pdf_builder,
            pdf_classifier=pdf_classifier,
            ocr_image_coverage=settings.ocr_image_coverage,
            ocr_min_text_quality=settings.ocr_min_text_quality,
            max_pages=settings.max_pages,
            ocr_concurrency=ocr_concurrency,
            ocr_dpi=settings.ocr_dpi,
            chat_model=ai_clients.judge,
            audit_log=audit_log,
            chunker=chunker,
            embedding_provider=ai_clients.embedding,
            chunk_repo=chunk_repo,
            entity_extractor=entity_extractor,
            entity_repo=entity_repo,
            lexical_term_extractor=lexical_term_extractor,
            lexical_terms_limit=settings.lexical_terms_limit,
            feature_repo=feature_repo,
            staged_ingestion=settings.staged_ingestion,
            stage_ledger=stage_ledger,
        )
        services.append(std)
        # Enhanced re-OCR bundle: same wiring, but a separate intake folder (ingest.enhanced/) and
        # the heavier extractor + higher DPI. The worker scans every services' ingest folder, so no
        # intake-loop change is needed.
        if enhanced_ocr is not None:
            enhanced_layout = FilesystemLayout(
                settings.files_root, tenant_id, ingest_dir="ingest.enhanced"
            )
            enhanced_layout.ensure()
            services.append(
                replace(
                    std,
                    layout=enhanced_layout,
                    ocr_extractor=enhanced_ocr,
                    ocr_dpi=settings.ocr_enhanced_dpi,
                )
            )
    return (
        services,
        reconciler,
        projection_runner,
        db,
        ocr_reload,
        ai_reload,
        post_reconcile,
        cleanup,
        heartbeat,
        is_quiesced,
    )
