"""FastAPI dependencies and lazy composition for the backend.

The job repository is resolved from the app's DI registry. If nothing is bound (production), a
Postgres-backed repository is created lazily on first use and cached, so the health endpoint and
tests that inject an in-memory repository never touch a database.
"""

from __future__ import annotations

from typing import cast

from doktok_contracts.ports import IngestionJobRepository
from fastapi import Request


def get_job_repository(request: Request) -> IngestionJobRepository:
    registry = request.app.state.registry
    if registry.is_registered(IngestionJobRepository):
        return cast(IngestionJobRepository, registry.resolve(IngestionJobRepository))

    # Lazy production wiring: build a Postgres-backed repository once and cache it.
    from doktok_storage_postgres import (
        Database,
        PostgresIngestionJobRepository,
        migrate,
    )

    settings = request.app.state.settings
    database = Database(settings.database_url)
    migrate(database)
    repository = PostgresIngestionJobRepository(database)
    registry.register(IngestionJobRepository, repository)
    request.app.state.database = database
    return repository
