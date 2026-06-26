"""PostgreSQL adapters and migrations."""

from doktok_storage_postgres.db import Database, migrate
from doktok_storage_postgres.repositories import (
    PostgresAppSettingsRepository,
    PostgresAuditLogRepository,
    PostgresCategoryRepository,
    PostgresChatThreadRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEmbeddingProjectionRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresLexicalTermExtractor,
    PostgresProjectionRequestRepository,
    PostgresRecordRepository,
    PostgresStatsRepository,
)

__version__ = "0.2.0"

__all__ = [
    "Database",
    "PostgresAppSettingsRepository",
    "PostgresAuditLogRepository",
    "PostgresCategoryRepository",
    "PostgresChatThreadRepository",
    "PostgresChunkRepository",
    "PostgresDocumentRepository",
    "PostgresEmbeddingProjectionRepository",
    "PostgresEntityRepository",
    "PostgresFeatureRepository",
    "PostgresIngestionJobRepository",
    "PostgresLexicalTermExtractor",
    "PostgresProjectionRequestRepository",
    "PostgresRecordRepository",
    "PostgresStatsRepository",
    "migrate",
]
