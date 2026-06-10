"""Worker composition root: wire ports to concrete adapters (ADR-0001)."""

from __future__ import annotations

from doktok_core.config import Settings
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import LibmagicMimeDetector
from doktok_storage_filesystem import (
    LocalFileStorage,
    QuarantineService,
    Sha256HashService,
)
from doktok_storage_postgres import Database, PostgresIngestionJobRepository, migrate


def build_services(settings: Settings) -> tuple[IngestionServices, Database]:
    """Build the ingestion services and a database handle. Ensures folders and runs migrations."""
    layout = FilesystemLayout(settings.files_root)
    layout.ensure()

    db = Database(settings.database_url)
    migrate(db)

    services = IngestionServices(
        job_repo=PostgresIngestionJobRepository(db),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=LibmagicMimeDetector(),
        security_policy=DefaultSecurityPolicy(max_file_mb=settings.max_file_mb),
        quarantine_service=QuarantineService(layout),
        layout=layout,
    )
    return services, db
