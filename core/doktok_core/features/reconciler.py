"""Feature reconciler: drive every active document to have every feature done (ADR-0009).

A stateless control loop. All state lives in the ``FeatureRepository`` ledger, and work is claimed
with SKIP-LOCKED semantics, so any number of worker instances can run this concurrently without
double-processing. Crashes are recovered via lease reclamation; failures retry with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from doktok_contracts.ports import FeatureProcessor, FeatureRepository

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
        lease_seconds: float = 900.0,
        max_per_pass: int = 1000,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._repo = feature_repo
        self._processors = {p.name: p for p in processors}
        self._registered = [(p.name, p.version) for p in processors]
        self._tenant_ids = list(tenant_ids)
        self._backoff_base = backoff_base_seconds
        self._lease_seconds = lease_seconds
        self._max_per_pass = max_per_pass
        self._clock = clock

    def reconcile(self) -> int:
        """One pass over all tenants. Returns how many feature runs completed this pass."""
        processed = 0
        for tenant_id in self._tenant_ids:
            self._repo.ensure_for_active(tenant_id, self._registered)
            processed += self._drain(tenant_id)
        return processed

    def _drain(self, tenant_id: str) -> int:
        processed = 0
        for _ in range(self._max_per_pass):
            now = self._clock()
            reclaim_before = now - timedelta(seconds=self._lease_seconds)
            row = self._repo.claim_next(tenant_id, now=now, reclaim_before=reclaim_before)
            if row is None:
                break
            processor = self._processors.get(row.feature)
            if processor is None:
                # No code for this feature (e.g. retired); don't leave the row stuck.
                self._repo.mark_done(row.id, feature_version=row.feature_version)
                continue
            try:
                processor.process(tenant_id, row.document_id)
                self._repo.mark_done(row.id, feature_version=processor.version)
                processed += 1
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
        return processed
