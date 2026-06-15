"""Feature reconciler: drive every active document to have every feature done (ADR-0009).

A stateless control loop. All state lives in the ``FeatureRepository`` ledger, and work is claimed
with SKIP-LOCKED semantics, so any number of worker instances can run this concurrently without
double-processing. Crashes are recovered via lease reclamation; failures retry with backoff.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from doktok_contracts.ports import AuditLogRepository, FeatureProcessor, FeatureRepository
from doktok_contracts.schemas import AuditEventType, DocumentFeature

from doktok_core.audit.logger import record_activity

logger = logging.getLogger("doktok.features")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FeatureReconciler:
    def __init__(
        self,
        feature_repo: FeatureRepository,
        processors: Sequence[FeatureProcessor],
        tenant_ids: Sequence[str],
        *,
        backoff_base_seconds: float = 30.0,
        # Above the worst-case feature runtime (an LLM extractor can do a primary + repair call,
        # each up to the ~600s Ollama timeout), so a slow-but-alive processor is not reclaimed and
        # double-run before it finishes.
        lease_seconds: float = 1800.0,
        max_per_pass: int = 1000,
        concurrency: int = 1,
        clock: Callable[[], datetime] = _utcnow,
        audit_log: AuditLogRepository | None = None,
    ) -> None:
        self._repo = feature_repo
        self._audit_log = audit_log
        self._processors = {p.name: p for p in processors}
        self._registered = [(p.name, p.version) for p in processors]
        # (feature, prerequisite) edges: a stage is only claimed once each prerequisite is done.
        self._dependencies: tuple[tuple[str, str], ...] = tuple(
            (p.name, prereq) for p in processors for prereq in getattr(p, "dependencies", ())
        )
        self._tenant_ids = list(tenant_ids)
        self._backoff_base = backoff_base_seconds
        self._lease_seconds = lease_seconds
        self._max_per_pass = max_per_pass
        self._concurrency = max(1, int(concurrency))
        self._clock = clock

    def reconcile(self) -> int:
        """One pass over all tenants. Returns how many feature runs completed this pass."""
        processed = 0
        for tenant_id in self._tenant_ids:
            self._repo.ensure_for_active(tenant_id, self._registered)
            processed += self._drain(tenant_id)
        return processed

    def recover_running(self) -> int:
        """Requeue feature rows left ``running`` by a previously killed worker (returns the count).

        Run once at startup: with no worker draining yet, any ``running`` row is an orphan, so this
        recovers it immediately rather than waiting out the per-row lease.
        """
        return sum(self._repo.requeue_running(tenant_id) for tenant_id in self._tenant_ids)

    def _claim(self, tenant_id: str) -> DocumentFeature | None:
        now = self._clock()
        reclaim_before = now - timedelta(seconds=self._lease_seconds)
        return self._repo.claim_next(
            tenant_id,
            now=now,
            reclaim_before=reclaim_before,
            dependencies=self._dependencies,
        )

    def _process(self, tenant_id: str, row: DocumentFeature) -> None:
        processor = self._processors.get(row.feature)
        if processor is None:
            # No code for this feature (e.g. retired); don't leave the row stuck.
            self._repo.mark_done(row.id, feature_version=row.feature_version)
            return
        try:
            processor.process(tenant_id, row.document_id)
            self._repo.mark_done(row.id, feature_version=processor.version)
            self._record(
                tenant_id,
                AuditEventType.FEATURE_COMPLETED,
                row,
                severity="info",
                description=f"{row.feature} completed",
                details={"feature": row.feature, "version": processor.version},
            )
        except Exception as exc:  # noqa: BLE001 - a feature failure retries, never crashes
            backoff = self._backoff_base * (2 ** max(0, row.attempts - 1))
            self._repo.mark_failed(
                row.id,
                error=str(exc),
                next_attempt_at=self._clock() + timedelta(seconds=backoff),
            )
            logger.warning(
                "feature %s failed for document %s (attempt %d): %s",
                row.feature,
                row.document_id,
                row.attempts,
                exc,
            )
            self._record(
                tenant_id,
                AuditEventType.FEATURE_FAILED,
                row,
                severity="error",
                description=f"{row.feature} failed: {exc}"[:240],
                details={"feature": row.feature, "error": str(exc), "attempt": row.attempts},
            )

    def _record(
        self,
        tenant_id: str,
        event_type: AuditEventType,
        row: DocumentFeature,
        *,
        severity: str,
        description: str,
        details: dict[str, object],
    ) -> None:
        """Emit one activity row for a feature run (non-fatal: never breaks reconciliation)."""
        if self._audit_log is None:
            return
        record_activity(
            self._audit_log,
            tenant_id,
            event_type,
            actor="reconciler",
            actor_kind="worker",
            document_id=row.document_id,
            severity=severity,
            phase="enrich",
            description=description,
            record_kind="feature",
            record_id=row.feature,
            details=details,
        )

    def _drain(self, tenant_id: str) -> int:
        if self._concurrency == 1:
            processed = 0
            for _ in range(self._max_per_pass):
                row = self._claim(tenant_id)
                if row is None:
                    break
                self._process(tenant_id, row)
                processed += 1
            return processed
        return self._drain_concurrent(tenant_id)

    def _drain_concurrent(self, tenant_id: str) -> int:
        # Several workers each claim a distinct row (FOR UPDATE SKIP LOCKED) and process it; the
        # processors operate on different documents and use thread-safe repos, so this is safe.
        lock = threading.Lock()
        budget = self._max_per_pass
        processed = 0
        drained = False

        def worker() -> None:
            nonlocal budget, processed, drained
            while True:
                with lock:
                    if drained or budget <= 0:
                        return
                    budget -= 1
                row = self._claim(tenant_id)
                if row is None:
                    with lock:
                        drained = True
                    return
                self._process(tenant_id, row)
                with lock:
                    processed += 1

        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            for _ in range(self._concurrency):
                pool.submit(worker)
        return processed
