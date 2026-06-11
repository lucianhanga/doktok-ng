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
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doktok_contracts.schemas import IngestionJob
from doktok_core.features.reconciler import FeatureReconciler
from doktok_core.ingestion.pipeline import IngestionServices, process_file, recover_stale_jobs
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
        reconcile_interval: float = 2.0,
        stale_job_minutes: float = 10.0,
        recover_interval: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # One IngestionServices per tenant (each carries that tenant's layout + tenant_id).
        self._services = list(services)
        self._tracker = StabilityTracker(stability_seconds)
        self._poll_interval = poll_interval
        self._concurrency = max(1, int(concurrency))
        self._reconciler = reconciler
        self._reconcile_interval = reconcile_interval
        self._stale_job_minutes = stale_job_minutes
        self._recover_interval = recover_interval
        self._clock = clock

    def reconcile(self) -> int:
        """Drive active documents toward having every registered feature processed (ADR-0009)."""
        if self._reconciler is None:
            return 0
        return self._reconciler.reconcile()

    def recover_stale(self) -> int:
        """Re-queue ingestion jobs abandoned mid-pipeline by a previously killed worker.

        Such jobs are stuck in a non-terminal state with their file stranded in in.process and never
        appear as documents; re-queuing puts the file back in ingest so it is reprocessed.
        """
        if self._stale_job_minutes <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(minutes=self._stale_job_minutes)
        total = 0
        for services in self._services:
            total += len(recover_stale_jobs(services, older_than=cutoff))
        if total:
            logger.warning("re-queued %d stale ingestion job(s) abandoned by a prior worker", total)
        return total

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
        if job.error_code or job.error_message:
            # Surface the failure reason (e.g. indexing_error / needs_ocr / quarantined) so the log
            # is actionable instead of a bare "status=failed".
            logger.warning(
                "ingested %s (tenant=%s) -> job %s status=%s reason=%s: %s",
                Path(path).name,
                services.tenant_id,
                job.id,
                job.status,
                job.error_code or "unknown",
                job.error_message or "",
            )
        else:
            logger.info(
                "ingested %s (tenant=%s) -> job %s status=%s",
                Path(path).name,
                services.tenant_id,
                job.id,
                job.status,
            )
        return job

    def run_forever(self) -> None:  # pragma: no cover - long-running loop
        """Run ingestion and feature reconciliation as independent, parallel streams.

        Ingestion (folder watching + the pipeline) and the feature reconciler are separate concerns
        that share only the thread-safe DB pool and the local Ollama server, so they run on their
        own threads - a large reconciler backfill no longer starves new ingestion (and vice versa).
        RAG runs in the backend process, a third independent stream.
        """
        tenants = ", ".join(s.tenant_id for s in self._services)
        logger.info("worker started; ingestion + reconciliation streams; tenants: %s", tenants)
        stop = threading.Event()
        threads = [
            threading.Thread(target=self._ingest_loop, args=(stop,), name="ingest", daemon=True)
        ]
        if self._reconciler is not None:
            threads.append(
                threading.Thread(
                    target=self._reconcile_loop, args=(stop,), name="reconcile", daemon=True
                )
            )
        for thread in threads:
            thread.start()
        try:
            while not stop.is_set():
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("worker stopping")
            stop.set()

    def _ingest_loop(self, stop: threading.Event) -> None:  # pragma: no cover - long-running loop
        # Recover anything a prior worker abandoned mid-pipeline before processing the live queue.
        self._safe_recover()
        last_recover = self._clock()
        while not stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the stream alive across unexpected errors
                logger.exception("ingestion scan failed")
            if self._clock() - last_recover >= self._recover_interval:
                self._safe_recover()
                last_recover = self._clock()
            stop.wait(self._poll_interval)

    def _safe_recover(self) -> None:  # pragma: no cover - long-running loop helper
        try:
            self.recover_stale()
        except Exception:  # noqa: BLE001 - recovery must never take down the ingestion stream
            logger.exception("stale-job recovery failed")

    def _reconcile_loop(
        self, stop: threading.Event
    ) -> None:  # pragma: no cover - long-running loop
        while not stop.is_set():
            processed = 0
            try:
                processed = self.reconcile()
            except Exception:  # noqa: BLE001 - keep the stream alive across unexpected errors
                logger.exception("reconcile pass failed")
            # Keep draining while there is work; back off to a poll cadence when idle.
            stop.wait(0.1 if processed else self._reconcile_interval)
