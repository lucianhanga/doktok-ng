"""PostgreSQL connection management and a lightweight SQL migration runner.

We use a small ordered-``.sql``-file migration runner (an Alembic-equivalent, ADR-0002) tracked in a
``schema_migrations`` table. Migrations are applied idempotently on startup.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from psycopg import Connection
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Fixed advisory-lock key so co-starting processes (backend + worker) serialize their migration run
# instead of racing on schema_migrations creation/inserts (APP-1).
_MIGRATION_LOCK_KEY = 778130


class Database:
    """Thin wrapper around a psycopg connection pool."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"autocommit": True},
        )

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        with self._pool.connection() as conn:
            yield conn

    def close(self) -> None:
        self._pool.close()


def migrate(db: Database, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply any pending migrations in filename order. Returns the versions applied this run."""
    applied_now: list[str] = []
    with db.connection() as conn:
        # Serialize concurrent migrators (backend + worker co-start) on a session advisory lock.
        # The connection is autocommit and returned to the pool afterwards, so unlock explicitly.
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            already = {row[0] for row in rows}

            for path in sorted(migrations_dir.glob("*.sql")):
                version = path.name
                if version in already:
                    continue
                sql = path.read_text(encoding="utf-8")
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied_now.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))
    return applied_now
