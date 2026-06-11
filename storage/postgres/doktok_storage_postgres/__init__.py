"""PostgreSQL adapters and migrations."""

from doktok_storage_postgres.db import Database, migrate
from doktok_storage_postgres.repositories import (
    PostgresAuditLogRepository,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresLexicalTermExtractor,
    PostgresRecordRepository,
    PostgresStatsRepository,
)

__version__ = "0.0.0"

__all__ = [
    "Database",
    "PostgresAuditLogRepository",
    "PostgresCategoryRepository",
    "PostgresChunkRepository",
    "PostgresDocumentRepository",
    "PostgresEntityRepository",
    "PostgresFeatureRepository",
    "PostgresIngestionJobRepository",
    "PostgresLexicalTermExtractor",
    "PostgresRecordRepository",
    "PostgresStatsRepository",
    "migrate",
]
