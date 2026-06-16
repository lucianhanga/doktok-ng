"""Worker entrypoint: ``python -m doktok_worker`` or ``uv run doktok-worker``."""

from __future__ import annotations

import logging
import signal
from types import FrameType

from doktok_core.config import get_settings
from doktok_core.logging_setup import configure_logging

from doktok_worker.composition import build_services
from doktok_worker.worker import IngestionWorker


def _install_sigterm_handler() -> None:
    """Convert SIGTERM into KeyboardInterrupt so a `make`/`kill` stop runs the same graceful
    shutdown path as Ctrl-C (atexit alone does not fire on SIGTERM) - this is what lets the OCR
    pool be torn down instead of leaked as orphan processes."""

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle)


def main() -> None:
    settings = get_settings()
    configure_logging(json_format=settings.log_format == "json", level=settings.log_level)
    _install_sigterm_handler()
    log = logging.getLogger("doktok.worker")
    services, reconciler, projection_runner, db, ocr_reload, cleanup, heartbeat = build_services(
        settings
    )
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
        heartbeat=heartbeat,
    )
    if settings.ingest_concurrency > 1:
        log.info("processing up to %d documents in parallel", settings.ingest_concurrency)
    try:
        worker.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        log.info("shutting down")
    finally:
        cleanup()  # tear down the OCR pool so its spawn workers do not leak as orphans
        db.close()


if __name__ == "__main__":
    main()
