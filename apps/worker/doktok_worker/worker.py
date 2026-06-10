"""Folder-watching ingestion worker (M1).

Polls the ingest folder, gates on file stability (ADR-0004), and runs the ingestion pipeline for
each stable file. The scan pass (``run_once``) is separated from the loop (``run_forever``) so it
can be unit-tested without timers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from doktok_contracts.schemas import IngestionJob
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.ingestion.stability import FileObservation, StabilityTracker

logger = logging.getLogger("doktok.worker")


class IngestionWorker:
    def __init__(
        self,
        services: IngestionServices,
        *,
        stability_seconds: float = 3.0,
        poll_interval: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._services = services
        self._tracker = StabilityTracker(stability_seconds)
        self._poll_interval = poll_interval
        self._clock = clock

    def run_once(self) -> list[IngestionJob]:
        """Scan the ingest folder once; ingest any files that have become stable."""
        ingest = self._services.layout.ingest
        if not ingest.exists():
            return []

        now = self._clock()
        results: list[IngestionJob] = []
        for entry in sorted(ingest.iterdir()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
            except FileNotFoundError:
                continue
            observation = FileObservation(path=str(entry), size=stat.st_size, mtime=stat.st_mtime)
            if not self._tracker.is_stable(observation, now):
                continue
            job = process_file(self._services, str(entry))
            self._tracker.forget(str(entry))
            logger.info("ingested %s -> job %s status=%s", entry.name, job.id, job.status)
            results.append(job)
        return results

    def run_forever(self) -> None:  # pragma: no cover - long-running loop
        logger.info("ingestion worker started; watching %s", self._services.layout.ingest)
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the worker alive across unexpected errors
                logger.exception("ingestion scan failed")
            time.sleep(self._poll_interval)
