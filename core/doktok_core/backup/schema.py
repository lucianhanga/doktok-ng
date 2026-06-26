"""Resolve the DB schema/migration generation for the portable backup manifest + restore gate.

The DokTok migration runner applies ordered ``NNNN_*.sql`` files (an Alembic-equivalent, ADR-0002);
the "schema generation" of a deployment is the highest migration number present in its migrations
directory. The portable backup stamps this into the manifest (``app_schema_version``) so a later
restore can REFUSE an archive produced by a NEWER schema than the running code (restoring a newer
dump into older code is unsafe) while accepting older-or-equal (which is migrated forward on apply).

This module is pure stdlib (no storage/infra import) so it stays inside the core layer: it is handed
the migrations directory path by the outer layer (the router knows where the package ships its SQL).
"""

from __future__ import annotations

import re
from pathlib import Path

_MIGRATION_RE = re.compile(r"^(\d+)_")


def schema_version_from_migrations(migrations_dir: Path) -> int:
    """The highest migration number under ``migrations_dir`` (e.g. ``0031_x.sql`` -> 31), or 0 when
    the directory is missing/empty or holds no numbered ``.sql`` files. Never raises."""
    if not migrations_dir.exists():
        return 0
    highest = 0
    try:
        for path in migrations_dir.glob("*.sql"):
            m = _MIGRATION_RE.match(path.name)
            if m:
                highest = max(highest, int(m.group(1)))
    except OSError:
        return 0
    return highest
