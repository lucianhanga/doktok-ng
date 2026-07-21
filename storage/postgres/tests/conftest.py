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
    or "postgresql://doktok:doktok@localhost:5433/doktok"
)


def _clean_test_tenants(database: Database) -> None:
    with database.connection() as conn:
        conn.execute("DELETE FROM audit_events WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_activity WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_chunks WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM kg_entity_aliases WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM kg_entity_mentions WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM kg_entities WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_entities WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_features WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM extracted_records WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_category_links WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM categories WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM document_tags WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM tags WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM embedding_projections WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM projection_requests WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM chat_messages WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM chat_threads WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM ingestion_jobs WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM documents WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM kg_merge_rejection WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM user_preferences WHERE tenant_id LIKE 'test%'")
        # Tenant/user registry (#554/#557); FK-safe: invitations + tokens -> users -> tenants.
        conn.execute("DELETE FROM invitations WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM api_tokens WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM users WHERE tenant_id LIKE 'test%'")
        conn.execute("DELETE FROM tenants WHERE id LIKE 'test%'")


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
