"""Worker composition root: wire ports to adapters, one bundle per tenant (ADR-0001, ADR-0007)."""

from __future__ import annotations

from doktok_contracts.ports import FeatureProcessor
from doktok_core.config import Settings
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.features.processors import (
    ChunkEmbedFeature,
    DocClassifyFeature,
    DocMetadataFeature,
    EntitiesFeature,
    StructuredRecordsFeature,
)
from doktok_core.features.reconciler import FeatureReconciler
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import (
    DirectTextExtractor,
    LibmagicMimeDetector,
    PyMuPdfClassifier,
    PyMuPdfRenderer,
    PyMuPdfTextExtractor,
    SearchablePdfBuilder,
)
from doktok_provider_ollama import (
    OllamaCategoryClassifier,
    OllamaChatModelProvider,
    OllamaEmbeddingProvider,
    OllamaMetadataExtractor,
    OllamaRecordExtractor,
    OllamaVisionOcr,
)
from doktok_storage_filesystem import (
    LocalFileStorage,
    QuarantineService,
    Sha256HashService,
)
from doktok_storage_postgres import (
    Database,
    PostgresAuditLogRepository,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresLexicalTermExtractor,
    PostgresRecordRepository,
    migrate,
)


def tenant_ids(settings: Settings) -> list[str]:
    """Unique tenant ids the worker should watch, derived from the token map."""
    seen: dict[str, None] = {}
    for tenant in settings.tenant_tokens.values():
        seen.setdefault(tenant, None)
    return list(seen)


def build_services(
    settings: Settings,
) -> tuple[list[IngestionServices], FeatureReconciler, Database]:
    """Build per-tenant ingestion services, the feature reconciler, and a shared database handle.

    Ensures each tenant's lifecycle folders exist and runs migrations once.
    """
    # Size the pool for concurrent pipelines (each holds a connection only briefly).
    db = Database(settings.database_url, max_size=max(4, settings.ingest_concurrency + 2))
    migrate(db)

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
    ocr_extractor = OllamaVisionOcr(
        settings.ocr_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=settings.ocr_num_ctx,
        num_predict=settings.ocr_num_predict,
        keep_alive=settings.ocr_keep_alive,
    )
    pdf_renderer = PyMuPdfRenderer()
    searchable_pdf_builder = SearchablePdfBuilder()
    pdf_classifier = PyMuPdfClassifier()
    chunker = FixedWindowChunker()
    embedding_provider = OllamaEmbeddingProvider(
        settings.embedding_model, settings.ollama_base_url, timeout=timeout
    )
    chunk_repo = PostgresChunkRepository(db)
    entity_extractor = RegexEntityExtractor()
    entity_repo = PostgresEntityRepository(db)
    lexical_term_extractor = PostgresLexicalTermExtractor(db)
    feature_repo = PostgresFeatureRepository(db)
    # The worker's chat model serves only the OCR-quality judge; point it at the judge model (dense,
    # shared with enrichment) so ingestion never loads the 23 GB qwen3.6 and evicts qwen3:14b.
    chat_model = OllamaChatModelProvider(
        settings.judge_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=settings.judge_num_ctx,
    )
    metadata_extractor = OllamaMetadataExtractor(
        settings.enrich_model,
        settings.enrich_repair_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=settings.enrich_num_ctx,
        think=settings.enrich_think,
    )
    category_classifier = OllamaCategoryClassifier(
        settings.enrich_model,
        settings.enrich_repair_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=settings.enrich_num_ctx,
        think=settings.enrich_think,
    )
    category_repo = PostgresCategoryRepository(db)
    record_extractor = OllamaRecordExtractor(
        settings.enrich_model,
        settings.enrich_repair_model,
        settings.ollama_base_url,
        timeout=timeout,
        num_ctx=settings.enrich_num_ctx,
        think=settings.enrich_think,
    )
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
        DocMetadataFeature(document_repo, file_storage, metadata_extractor),
        DocClassifyFeature(document_repo, file_storage, category_classifier, category_repo),
        StructuredRecordsFeature(document_repo, file_storage, record_extractor, record_repo),
    ]
    reconciler = FeatureReconciler(feature_repo, processors, tenant_ids(settings))

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
            )
        )
    return services, reconciler, db
