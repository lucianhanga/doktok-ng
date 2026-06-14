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
    log = logging.getLogger("doktok.worker")
    services, reconciler, projection_runner, db, ocr_reload = build_services(settings)
    if not services:
        log.warning(
            "no tenants configured (DOKTOK_TENANT_TOKENS is empty); the worker has nothing to watch"
        )
    worker = IngestionWorker(
        services,
        stability_seconds=settings.file_stability_seconds,
        concurrency=settings.ingest_concurrency,
        reconciler=reconciler,
        stale_job_minutes=settings.stale_job_minutes,
        projection_runner=projection_runner,
        ocr_reload=ocr_reload,
    )
    if settings.ingest_concurrency > 1:
        log.info("processing up to %d documents in parallel", settings.ingest_concurrency)
    try:
        worker.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        logging.getLogger("doktok.worker").info("shutting down")
    finally:
        db.close()


if __name__ == "__main__":
    main()
