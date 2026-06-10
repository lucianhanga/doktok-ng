"""Worker entrypoint: ``python -m doktok_worker`` or ``uv run doktok-worker``."""

from __future__ import annotations

import logging

from doktok_core.config import get_settings

from doktok_worker.composition import build_services
from doktok_worker.worker import IngestionWorker


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    services, db = build_services(settings)
    worker = IngestionWorker(
        services,
        stability_seconds=settings.file_stability_seconds,
    )
    try:
        worker.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        logging.getLogger("doktok.worker").info("shutting down")
    finally:
        db.close()


if __name__ == "__main__":
    main()
