"""PostgreSQL adapters and migrations."""

from doktok_storage_postgres.db import Database, migrate
from doktok_storage_postgres.repositories import (
    PostgresAuditLogRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresFeatureRepository,
    PostgresIngestionJobRepository,
    PostgresLexicalTermExtractor,
    PostgresStatsRepository,
)

__version__ = "0.0.0"

__all__ = [
    "Database",
    "PostgresAuditLogRepository",
    "PostgresChunkRepository",
    "PostgresDocumentRepository",
    "PostgresEntityRepository",
    "PostgresFeatureRepository",
    "PostgresIngestionJobRepository",
    "PostgresLexicalTermExtractor",
    "PostgresStatsRepository",
    "migrate",
]
