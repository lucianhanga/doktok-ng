"""Backend entrypoint.

- ``python -m doktok_api``          -> run the API server (binds settings.bind_host)
- ``python -m doktok_api migrate``  -> apply pending DB migrations, then exit (a fail-fast,
  pre-traffic deploy step so the first request never pays migration latency; APP-1)
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn
from doktok_core.config import get_settings

logger = logging.getLogger("doktok.backend")


def migrate() -> int:
    """Apply pending migrations and return a process exit code (0 ok, 1 failure)."""
    from doktok_storage_postgres.db import Database
    from doktok_storage_postgres.db import migrate as run_migrate

    settings = get_settings()
    db = Database(settings.database_url)
    try:
        applied = run_migrate(db)
    except Exception:  # noqa: BLE001 - surface any migration failure as a non-zero exit
        logger.exception("migration failed")
        return 1
    finally:
        db.close()
    if applied:
        logger.info("applied %d migration(s): %s", len(applied), ", ".join(applied))
    else:
        logger.info("no pending migrations")
    return 0


def serve() -> None:
    settings = get_settings()
    uvicorn.run(
        "doktok_api.main:app",
        host=settings.bind_host,
        port=int(os.environ.get("DOKTOK_PORT", "8000")),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        sys.exit(migrate())
    serve()


if __name__ == "__main__":
    main()
