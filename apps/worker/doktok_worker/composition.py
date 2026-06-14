"""Worker composition root: wire ports to adapters, one bundle per tenant (ADR-0001, ADR-0007)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from doktok_contracts.ports import (
    CategoryClassifier,
    EntityNerExtractor,
    FeatureProcessor,
    MetadataExtractor,
    OcrExtractor,
    RecordExtractor,
)
from doktok_core.config import Settings
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.extraction.service import ExtractionResult, extract_document
from doktok_core.features.processors import (
    ChunkEmbedFeature,
    DocClassifyFeature,
    DocMetadataFeature,
    EntitiesFeature,
    NerFeature,
    StructuredRecordsFeature,
    ThumbnailFeature,
)
from doktok_core.features.reconciler import FeatureReconciler
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.extract_stage import ExtractStage
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_core.settings.catalog import ollama_think_for, openai_reasoning_effort
from doktok_core.visualizations.service import ProjectionRunner, ProjectionService
from doktok_modalities_files import (
    DirectTextExtractor,
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
    OllamaVisionOcr,
)
from doktok_provider_openai import (
    OpenAiCategoryClassifier,
    OpenAiEntityNerExtractor,
    OpenAiMetadataExtractor,
    OpenAiRecordExtractor,
)
from doktok_provider_paddleocr import PaddleOcr
from doktok_provider_projection import SklearnEmbeddingProjector
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
    PostgresLexicalTermExtractor,
    PostgresProjectionRequestRepository,
    PostgresRecordRepository,
    migrate,
)

logger = logging.getLogger("doktok.worker")


def tenant_ids(settings: Settings) -> list[str]:
    """Unique tenant ids the worker should watch, derived from the token map."""
    seen: dict[str, None] = {}
    for tenant in settings.tenant_tokens.values():
        seen.setdefault(tenant, None)
    return list(seen)


def build_services(
    settings: Settings,
) -> tuple[
    list[IngestionServices],
    FeatureReconciler,
    ProjectionRunner,
    Database,
    Callable[[], None] | None,
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
    app_settings = PostgresAppSettingsRepository(db)
    pipeline = app_settings.get_ai_settings().pipeline
    openai_key = app_settings.get_openai_api_key()
    # OCR parallelism comes from the Settings DB (live-reloaded by the worker), env is the fallback
    # default. This is the number of OCR worker processes directly - if more documents ingest at
    # once than this, their pages just share the pool (the process pool queues the extra work).
    ocr_concurrency = app_settings.get_ocr_settings().ocr_concurrency
    use_openai_pipeline = pipeline.provider == "openai" and bool(openai_key)
    if pipeline.provider == "openai" and not openai_key:
        logger.warning("pipeline set to OpenAI but no API key configured; using Ollama defaults")

    job_repo = PostgresIngestionJobRepository(db)
    document_repo = PostgresDocumentRepository(db)
    audit_log = PostgresAuditLogRepository(db)
    file_storage = LocalFileStorage()
    hash_service = Sha256HashService()
    mime_detector = LibmagicMimeDetector()
    security_policy = DefaultSecurityPolicy(max_file_mb=settings.max_file_mb)
    text_extractor = DirectTextExtractor()
    pdf_extractor = PyMuPdfTextExtractor()
    timeout = settings.ollama_timeout_seconds
    ocr_extractor: OcrExtractor
    if settings.ocr_engine == "paddleocr":
        # `ocr_concurrency` independent predictors = the number of pages OCR'd in parallel across
        # the whole worker (PaddleOCR is CPU-bound, ~1 core each). Set directly by the OCR setting.
        ocr_extractor = PaddleOcr(lang=settings.ocr_lang, pool_size=ocr_concurrency)
    else:
        ocr_extractor = OllamaVisionOcr(
            settings.ocr_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=settings.ocr_num_ctx,
            num_predict=settings.ocr_num_predict,
            keep_alive=settings.ocr_keep_alive,
        )
    # Live-reload OCR parallelism from Settings (M7.6): the worker calls this between ingest scans
    # (no OCR in flight) to resize the PaddleOCR pool without a restart. Paddle-only; the Ollama OCR
    # path has no predictor pool to resize.
    ocr_reload: Callable[[], None] | None = None
    if isinstance(ocr_extractor, PaddleOcr):
        paddle = ocr_extractor

        def ocr_reload() -> None:  # noqa: F811 - single definition, guarded by the isinstance
            paddle.reconfigure(app_settings.get_ocr_settings().ocr_concurrency)

    pdf_renderer = PyMuPdfRenderer()
    searchable_pdf_builder = SearchablePdfBuilder()
    thumbnailer = PyMuPdfThumbnailer()
    pdf_classifier = PyMuPdfClassifier()
    chunker = FixedWindowChunker()
    embedding_provider = OllamaEmbeddingProvider(
        settings.embedding_model,
        settings.ollama_base_url,
        timeout=timeout,
        keep_alive=settings.embedding_keep_alive,
    )
    chunk_repo = PostgresChunkRepository(db)
    entity_extractor = RegexEntityExtractor()
    entity_repo = PostgresEntityRepository(db)
    lexical_term_extractor = PostgresLexicalTermExtractor(db)
    feature_repo = PostgresFeatureRepository(db)
    # The worker's chat model serves only the OCR-quality judge. Point it at the SAME model (and
    # context) the pipeline/enrichment uses, so the worker keeps a single large model resident
    # instead of loading a second one and thrashing GPU memory under a tight budget - which evicts
    # the in-use model and stalls the single-threaded reconciler. When the pipeline runs on OpenAI
    # (remote), the judge stays on the local judge model.
    judge_model = (
        settings.judge_model
        if use_openai_pipeline
        else (pipeline.model if pipeline.provider == "ollama" else settings.enrich_model)
    )
    judge_num_ctx = settings.judge_num_ctx if use_openai_pipeline else pipeline.num_ctx
    chat_model = OllamaChatModelProvider(
        judge_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=judge_num_ctx,
        keep_alive=settings.enrich_keep_alive,
    )
    metadata_extractor: MetadataExtractor
    category_classifier: CategoryClassifier
    record_extractor: RecordExtractor
    ner_extractor: EntityNerExtractor
    if use_openai_pipeline:
        logger.info(
            "pipeline extraction via OpenAI %s (egress enabled by AI settings)", pipeline.model
        )
        effort = openai_reasoning_effort(pipeline.reasoning, pipeline.model)
        metadata_extractor = OpenAiMetadataExtractor(
            pipeline.model, openai_key, timeout=timeout, reasoning_effort=effort
        )
        category_classifier = OpenAiCategoryClassifier(
            pipeline.model, openai_key, timeout=timeout, reasoning_effort=effort
        )
        record_extractor = OpenAiRecordExtractor(
            pipeline.model, openai_key, timeout=timeout, reasoning_effort=effort
        )
        ner_extractor = OpenAiEntityNerExtractor(
            pipeline.model, openai_key, timeout=timeout, reasoning_effort=effort
        )
    else:
        # Ollama path: the selected Ollama model, or the env default if OpenAI lacks a key.
        p_model = pipeline.model if pipeline.provider == "ollama" else settings.enrich_model
        p_ctx = pipeline.num_ctx if pipeline.provider == "ollama" else settings.enrich_num_ctx
        p_think = ollama_think_for(pipeline.reasoning, p_model, structured=True)
        metadata_extractor = OllamaMetadataExtractor(
            p_model,
            settings.enrich_repair_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=p_ctx,
            think=p_think,
            keep_alive=settings.enrich_keep_alive,
        )
        category_classifier = OllamaCategoryClassifier(
            p_model,
            settings.enrich_repair_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=p_ctx,
            think=p_think,
            keep_alive=settings.enrich_keep_alive,
        )
        record_extractor = OllamaRecordExtractor(
            p_model,
            settings.enrich_repair_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=p_ctx,
            think=p_think,
            keep_alive=settings.enrich_keep_alive,
        )
        ner_extractor = OllamaEntityNerExtractor(
            p_model,
            settings.enrich_repair_model,
            settings.ollama_base_url,
            timeout=timeout,
            num_ctx=p_ctx,
            think=p_think,
            keep_alive=settings.enrich_keep_alive,
        )
    category_repo = PostgresCategoryRepository(db)
    record_repo = PostgresRecordRepository(db)

    # Reconciler processors re-derive from stored artifacts, so they share the same adapters.
    processors: list[FeatureProcessor] = [
        ChunkEmbedFeature(document_repo, file_storage, chunker, embedding_provider, chunk_repo),
        EntitiesFeature(
            document_repo,
            file_storage,
            entity_extractor,
            lexical_term_extractor,
            entity_repo,
            lexical_terms_limit=settings.lexical_terms_limit,
        ),
        NerFeature(document_repo, file_storage, ner_extractor, entity_repo),
        DocMetadataFeature(document_repo, file_storage, metadata_extractor),
        DocClassifyFeature(document_repo, file_storage, category_classifier, category_repo),
        StructuredRecordsFeature(document_repo, file_storage, record_extractor, record_repo),
        ThumbnailFeature(document_repo, file_storage, thumbnailer),
    ]

    # Staged ingestion (ADR-0015): the `extract` stage runs OCR/extraction + activation in the
    # reconciler. Register it ahead of the feature stages when enabled; the stage ledger (seeded at
    # intake) is exactly the registered stages, so they all get a row and the dependency gate orders
    # extract -> features.
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
            chat_model=chat_model,
            max_pages=settings.max_pages,
            ocr_concurrency=ocr_concurrency,
        )

    if settings.staged_ingestion:
        processors.insert(
            0, ExtractStage(document_repo, file_storage, settings.files_root, _extract)
        )
    stage_ledger = [(p.name, p.version) for p in processors]

    # Fan the reconciler wider when the pipeline is remote (OpenAI): its enrichment features are
    # network-bound and the API parallelizes well, whereas the local Ollama path thrashes a single
    # GPU under high concurrency.
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
    )

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
        services.append(
            IngestionServices(
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
                ocr_extractor=ocr_extractor,
                pdf_renderer=pdf_renderer,
                searchable_pdf_builder=searchable_pdf_builder,
                pdf_classifier=pdf_classifier,
                ocr_image_coverage=settings.ocr_image_coverage,
                ocr_min_text_quality=settings.ocr_min_text_quality,
                max_pages=settings.max_pages,
                ocr_concurrency=ocr_concurrency,
                chat_model=chat_model,
                audit_log=audit_log,
                chunker=chunker,
                embedding_provider=embedding_provider,
                chunk_repo=chunk_repo,
                entity_extractor=entity_extractor,
                entity_repo=entity_repo,
                lexical_term_extractor=lexical_term_extractor,
                lexical_terms_limit=settings.lexical_terms_limit,
                feature_repo=feature_repo,
                staged_ingestion=settings.staged_ingestion,
                stage_ledger=stage_ledger,
            )
        )
    return services, reconciler, projection_runner, db, ocr_reload
