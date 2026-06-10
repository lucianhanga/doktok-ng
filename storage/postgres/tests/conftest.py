"""Shared fixtures for Postgres integration tests.

These tests run against a real database, but ONLY ever touch tenants whose id starts with ``test``,
so they never delete another tenant's data (e.g. ``developer`` / ``default``) when the suite runs
against your local DB. Point ``DOKTOK_TEST_DATABASE_URL`` at a separate database for full isolation;
otherwise they fall back to ``DOKTOK_DATABASE_URL``. Skipped automatically if no DB is reachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from doktok_storage_postgres import Database, migrate

TEST_TENANT = "test"
TEST_TENANT_A = "test-a"
TEST_TENANT_B = "test-b"

DSN = (
    os.environ.get("DOKTOK_TEST_DATABASE_URL")
    or os.environ.get("DOKTOK_DATABASE_URL")
    or "postgresql://doktok:doktok@localhost:5432/doktok"
)


def _clean_test_tenants(database: Database) -> None:
    with database.connection() as conn:
        conn.execute("DELETE FROM audit_events WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM ingestion_jobs WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM documents WHERE tenant_id LIKE 'test%'")


@pytest.fixture
def db() -> Iterator[Database]:
    try:
        with psycopg.connect(DSN, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres not reachable: {exc}")
    database = Database(DSN)
    migrate(database)
    _clean_test_tenants(database)
    yield database
    _clean_test_tenants(database)
    database.close()
