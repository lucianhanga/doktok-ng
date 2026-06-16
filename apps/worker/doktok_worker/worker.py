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
from doktok_core.visualizations.service import ProjectionRunner

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
        projection_runner: ProjectionRunner | None = None,
        projection_interval: float = 5.0,
        ocr_reload: Callable[[], None] | None = None,
        ocr_reload_interval: float = 15.0,
        heartbeat: Callable[[], None] | None = None,
        heartbeat_interval: float = 15.0,
        is_quiesced: Callable[[], bool] | None = None,
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
        self._projection_runner = projection_runner
        self._projection_interval = projection_interval
        # Live OCR-pool resize from Settings (M7.6): invoked between ingest scans.
        self._ocr_reload = ocr_reload
        self._ocr_reload_interval = ocr_reload_interval
        # Liveness heartbeat (APP-5): stamped periodically so an external probe can spot a dead one.
        self._heartbeat = heartbeat
        self._heartbeat_interval = heartbeat_interval
        # Quiesce/maintenance gate (APP-C3): when on, start no new ingestion/reconcile work so a
        # backup can capture a still DB + files_root pair; in-flight work finishes normally.
        self._is_quiesced = is_quiesced
        self._quiesced_state = False
        self._clock = clock

    def _quiesced(self) -> bool:
        if self._is_quiesced is None:
            return False
        try:
            q = self._is_quiesced()
        except Exception:  # noqa: BLE001 - a quiesce-check failure must never stop the worker
            return False
        if q != self._quiesced_state:
            logger.info("maintenance mode %s", "ON - pausing new work" if q else "OFF - resuming")
            self._quiesced_state = q
        return q

    def run_projections(self) -> int:
        """Drain the embedding-projection recompute queue (Insights tab, ADR-0016)."""
        if self._projection_runner is None:
            return 0
        return self._projection_runner.run_pending()

    def reconcile(self) -> int:
        """Drive active documents toward having every registered feature processed (ADR-0009)."""
        if self._reconciler is None or self._quiesced():
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
        if self._quiesced():  # maintenance mode: start no new ingestion (APP-C3)
            return []
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
        if self._projection_runner is not None:
            threads.append(
                threading.Thread(
                    target=self._projection_loop, args=(stop,), name="projection", daemon=True
                )
            )
        for thread in threads:
            thread.start()
        try:
            last_heartbeat = 0.0
            while not stop.is_set():
                if self._heartbeat is not None and self._clock() - last_heartbeat >= (
                    self._heartbeat_interval
                ):
                    self._safe_heartbeat()
                    last_heartbeat = self._clock()
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("worker stopping")
            stop.set()

    def _safe_heartbeat(self) -> None:  # pragma: no cover - long-running loop helper
        if self._heartbeat is None:
            return
        try:
            self._heartbeat()
        except Exception:  # noqa: BLE001 - a heartbeat write must never take down the worker
            logger.exception("worker heartbeat failed")

    def _ingest_loop(self, stop: threading.Event) -> None:  # pragma: no cover - long-running loop
        # Recover anything a prior worker abandoned mid-pipeline before processing the live queue.
        self._safe_recover()
        last_recover = self._clock()
        last_ocr_reload = 0.0
        while not stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - keep the stream alive across unexpected errors
                logger.exception("ingestion scan failed")
            if self._clock() - last_recover >= self._recover_interval:
                self._safe_recover()
                last_recover = self._clock()
            # Apply a Settings change to OCR parallelism here: run_once has returned, so no page is
            # being OCR'd and the pool can be safely resized. Throttled to keep the DB read cheap.
            if self._clock() - last_ocr_reload >= self._ocr_reload_interval:
                self._safe_ocr_reload()
                last_ocr_reload = self._clock()
            stop.wait(self._poll_interval)

    def _safe_recover(self) -> None:  # pragma: no cover - long-running loop helper
        try:
            self.recover_stale()
        except Exception:  # noqa: BLE001 - recovery must never take down the ingestion stream
            logger.exception("stale-job recovery failed")

    def _safe_ocr_reload(self) -> None:  # pragma: no cover - long-running loop helper
        if self._ocr_reload is None:
            return
        try:
            self._ocr_reload()
        except Exception:  # noqa: BLE001 - an OCR-resize failure must not stop ingestion
            logger.exception("OCR pool reload failed")

    def _recover_features(self) -> None:  # pragma: no cover - long-running loop helper
        if self._reconciler is None:
            return
        try:
            recovered = self._reconciler.recover_running()
            if recovered:
                logger.warning("re-queued %d feature(s) left running by a prior worker", recovered)
        except Exception:  # noqa: BLE001 - recovery must never take down the reconcile stream
            logger.exception("stale-feature recovery failed")

    def _reconcile_loop(
        self, stop: threading.Event
    ) -> None:  # pragma: no cover - long-running loop
        # Recover features left mid-flight by a prior worker before draining the live backlog, so a
        # restart doesn't leave them stuck for the full lease window.
        self._recover_features()
        while not stop.is_set():
            processed = 0
            try:
                processed = self.reconcile()
            except Exception:  # noqa: BLE001 - keep the stream alive across unexpected errors
                logger.exception("reconcile pass failed")
            # Keep draining while there is work; back off to a poll cadence when idle.
            stop.wait(0.1 if processed else self._reconcile_interval)

    def _projection_loop(
        self, stop: threading.Event
    ) -> None:  # pragma: no cover - long-running loop
        # A separate stream: fitting UMAP is CPU-heavy and must not stall ingestion/reconciliation.
        if self._projection_runner is not None:
            try:
                self._projection_runner.prewarm()  # warm UMAP/HDBSCAN JIT off the first recompute
            except Exception:  # noqa: BLE001 - pre-warming must never take down the stream
                logger.exception("projection pre-warm failed")
        while not stop.is_set():
            processed = 0
            try:
                processed = self.run_projections()
            except Exception:  # noqa: BLE001 - keep the stream alive across unexpected errors
                logger.exception("projection recompute pass failed")
            stop.wait(0.1 if processed else self._projection_interval)
