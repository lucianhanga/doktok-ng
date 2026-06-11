"""Folder-watching ingestion worker (M1, multi-tenant in M1.5).

Polls each tenant's ingest folder, gates on file stability (ADR-0004), and runs the ingestion
pipeline for each stable file (tagging the resulting job with that tenant, ADR-0007). The scan pass
(``run_once``) is separated from the loop (``run_forever``) so it can be unit-tested without timers.

Stable files can be processed concurrently (``concurrency`` > 1) to improve throughput, since the
pipeline is mostly IO-bound (DB, Ollama, file IO). Stability tracking stays single-threaded; only
the independent per-file ``process_file`` calls run in a thread pool. The Postgres pool is
thread-safe and each job uses its own per-job working dir, so concurrent files do not interfere.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from doktok_contracts.schemas import IngestionJob
from doktok_core.features.reconciler import FeatureReconciler
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.ingestion.stability import FileObservation, StabilityTracker

logger = logging.getLogger("doktok.worker")

_Pending = tuple[IngestionServices, str]


class IngestionWorker:
    def __init__(
        self,
        services: Sequence[IngestionServices],
        *,
        stability_seconds: float = 3.0,
        poll_interval: float = 1.0,
        concurrency: int = 1,
        reconciler: FeatureReconciler | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # One IngestionServices per tenant (each carries that tenant's layout + tenant_id).
        self._services = list(services)
        self._tracker = StabilityTracker(stability_seconds)
        self._poll_interval = poll_interval
        self._concurrency = max(1, int(concurrency))
        self._reconciler = reconciler
        self._clock = clock

    def reconcile(self) -> int:
        """Drive active documents toward having every registered feature processed (ADR-0009)."""
        if self._reconciler is None:
            return 0
        return self._reconciler.reconcile()

    def run_once(self) -> list[IngestionJob]:
        """Scan every tenant's ingest folder once; ingest files that have become stable."""
        now = self._clock()
        # Stability checks + tracker mutations happen single-threaded; collect ready files first.
        pending: list[_Pending] = []
        for services in self._services:
            pending.extend(self._collect_stable(services, now))
        if not pending:
            return []
        if self._concurrency == 1 or len(pending) == 1:
            return [self._process(services, path) for services, path in pending]
        with ThreadPoolExecutor(max_workers=min(self._concurrency, len(pending))) as pool:
            return list(pool.map(self._process_pair, pending))

    def _process_pair(self, item: _Pending) -> IngestionJob:
        return self._process(*item)

    def _collect_stable(self, services: IngestionServices, now: float) -> list[_Pending]:
        ingest = services.layout.ingest
        if not ingest.exists():
            return []
        ready: list[_Pending] = []
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
            self._tracker.forget(str(entry))
            ready.append((services, str(entry)))
        return ready

    def _process(self, services: IngestionServices, path: str) -> IngestionJob:
        job = process_file(services, path)
        logger.info(
            "ingested %s (tenant=%s) -> job %s status=%s",
            Path(path).name,
            services.tenant_id,
            job.id,
            job.status,
        )
        return job

    def run_forever(self) -> None:  # pragma: no cover - long-running loop
        tenants = ", ".join(s.tenant_id for s in self._services)
        logger.info("ingestion worker started; tenants: %s", tenants)
        while True:
            try:
                self.run_once()
                self.reconcile()
            except Exception:  # noqa: BLE001 - keep the worker alive across unexpected errors
                logger.exception("ingestion scan failed")
            time.sleep(self._poll_interval)
