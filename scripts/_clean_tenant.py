"""Delete all DB rows + files for one tenant. Invoked by clean-tenant.sh (tenant in env).

Tenant-scoped table names come from a fixed allowlist (never user input); the tenant id is always
passed as a bound parameter, so this is safe from SQL injection.
"""

from __future__ import annotations

import os
import shutil
import sys

from doktok_core.config import get_settings
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_storage_postgres import Database

# Every tenant-scoped table, child rows first.
_TENANT_TABLES = [
    "document_features",
    "extracted_records",
    "document_category_links",
    "categories",  # no document FK, so must be deleted explicitly (not cascaded)
    "embedding_projections",  # derived cache; points cascade from the header
    "projection_requests",
    "document_entities",
    "document_chunks",
    "audit_events",
    "documents",
    "ingestion_jobs",
    "tenant_ai_settings",  # the tenant's model-stack override (#708)
]


def main() -> int:
    tenant = os.environ.get("DOKTOK_CLEAN_TENANT", "").strip()
    if not tenant:
        print("DOKTOK_CLEAN_TENANT is not set", file=sys.stderr)
        return 1

    settings = get_settings()

    db = Database(settings.database_url)
    deleted: dict[str, int] = {}
    try:
        with db.connection() as conn:
            for table in _TENANT_TABLES:
                count = conn.execute(
                    f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)
                ).fetchone()[0]
                conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (tenant,))
                deleted[table] = int(count)
            # Tenant-scoped secrets in the global KV store (#719: the tenant's own OpenAI key).
            key_count = conn.execute(
                "SELECT count(*) FROM app_settings WHERE key LIKE %s", (f"tenant:{tenant}:%",)
            ).fetchone()[0]
            conn.execute("DELETE FROM app_settings WHERE key LIKE %s", (f"tenant:{tenant}:%",))
            deleted["app_settings(tenant:*)"] = int(key_count)
    finally:
        db.close()

    nonzero = {t: n for t, n in deleted.items() if n}
    print(f"  database rows deleted: {nonzero or 'none'}")

    layout = FilesystemLayout(settings.files_root, tenant)
    existed = layout.base.exists()
    shutil.rmtree(layout.base, ignore_errors=True)
    layout.ensure()
    print(f"  files: {'wiped and folders recreated' if existed else 'created empty folders'}")
    print(f"  storage root: {layout.base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
