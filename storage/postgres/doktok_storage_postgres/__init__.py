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
    PostgresKnowledgeGraphRepository,
    PostgresLexicalTermExtractor,
    PostgresMemoryRepository,
    PostgresProjectionRequestRepository,
    PostgresRecordRepository,
    PostgresStatsRepository,
    PostgresTenantRegistry,
    PostgresUserPreferenceRepository,
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
    "PostgresKnowledgeGraphRepository",
    "PostgresLexicalTermExtractor",
    "PostgresMemoryRepository",
    "PostgresProjectionRequestRepository",
    "PostgresRecordRepository",
    "PostgresStatsRepository",
    "PostgresTenantRegistry",
    "PostgresUserPreferenceRepository",
    "migrate",
]
