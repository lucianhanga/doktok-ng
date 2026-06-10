"""Worker composition root: wire ports to adapters, one bundle per tenant (ADR-0001, ADR-0007)."""

from __future__ import annotations

from doktok_core.config import Settings
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import (
    DirectTextExtractor,
    LibmagicMimeDetector,
    PyMuPdfRenderer,
    PyMuPdfTextExtractor,
    SearchablePdfBuilder,
)
from doktok_provider_ollama import OllamaVisionOcr
from doktok_storage_filesystem import (
    LocalFileStorage,
    QuarantineService,
    Sha256HashService,
)
from doktok_storage_postgres import (
    Database,
    PostgresDocumentRepository,
    PostgresIngestionJobRepository,
    migrate,
)


def tenant_ids(settings: Settings) -> list[str]:
    """Unique tenant ids the worker should watch, derived from the token map."""
    seen: dict[str, None] = {}
    for tenant in settings.tenant_tokens.values():
        seen.setdefault(tenant, None)
    return list(seen)


def build_services(settings: Settings) -> tuple[list[IngestionServices], Database]:
    """Build per-tenant ingestion services and a shared database handle.

    Ensures each tenant's lifecycle folders exist and runs migrations once.
    """
    db = Database(settings.database_url)
    migrate(db)

    job_repo = PostgresIngestionJobRepository(db)
    document_repo = PostgresDocumentRepository(db)
    file_storage = LocalFileStorage()
    hash_service = Sha256HashService()
    mime_detector = LibmagicMimeDetector()
    security_policy = DefaultSecurityPolicy(max_file_mb=settings.max_file_mb)
    text_extractor = DirectTextExtractor()
    pdf_extractor = PyMuPdfTextExtractor()
    ocr_extractor = OllamaVisionOcr(settings.ocr_model, settings.ollama_base_url)
    pdf_renderer = PyMuPdfRenderer()
    searchable_pdf_builder = SearchablePdfBuilder()

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
            )
        )
    return services, db
