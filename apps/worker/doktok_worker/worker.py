"""Folder-watching ingestion worker (M1, multi-tenant in M1.5).

Polls each tenant's ingest folder, gates on file stability (ADR-0004), and runs the ingestion
pipeline for each stable file (tagging the resulting job with that tenant, ADR-0007). The scan pass
(``run_once``) is separated from the loop (``run_forever``) so it can be unit-tested without timers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from doktok_contracts.schemas import IngestionJob
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.ingestion.stability import FileObservation, StabilityTracker

logger = logging.getLogger("doktok.worker")


class IngestionWorker:
    def __init__(
        self,
        services: Sequence[IngestionServices],
        *,
        stability_seconds: float = 3.0,
        poll_interval: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # One IngestionServices per tenant (each carries that tenant's layout + tenant_id).
        self._services = list(services)
        self._tracker = StabilityTracker(stability_seconds)
        self._poll_interval = poll_interval
        self._clock = clock

    def run_once(self) -> list[IngestionJob]:
        """Scan every tenant's ingest folder once; ingest files that have become stable."""
        now = self._clock()
        results: list[IngestionJob] = []
        for services in self._services:
            results.extend(self._scan_tenant(services, now))
        return results

    def _scan_tenant(self, services: IngestionServices, now: float) -> list[IngestionJob]:
        ingest = services.layout.ingest
        if not ingest.exists():
            return []
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
            job = process_file(services, str(entry))
            self._tracker.forget(str(entry))
            logger.info(
                "ingested %s (tenant=%s) -> job %s status=%s",
                entry.name,
                services.tenant_id,
                job.id,
                job.status,
            )
            results.append(job)
        return results

    def run_forever(self) -> None:  # pragma: no cover - long-running loop
        tenants = ", ".join(s.tenant_id for s in self._services)
        logger.info("ingestion worker started; tenants: %s", tenants)
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the worker alive across unexpected errors
                logger.exception("ingestion scan failed")
            time.sleep(self._poll_interval)
