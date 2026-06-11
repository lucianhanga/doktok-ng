"""FeatureReconciler unit tests with the in-memory ledger (ADR-0009)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import DocumentFeature, FeatureStatus
from doktok_core.features.inmemory import InMemoryFeatureRepository
from doktok_core.features.reconciler import FeatureReconciler

BASE = datetime(2026, 1, 1, tzinfo=UTC)


class FakeProcessor:
    name = "demo"
    version = 1

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self._fail_times = fail_times

    def process(self, tenant_id: str, document_id: str) -> None:  # noqa: ARG002
        self.calls.append(document_id)
        if len(self.calls) <= self._fail_times:
            raise RuntimeError("boom")


def _advancing_clock(step_hours: int = 1) -> Callable[[], datetime]:
    counter = itertools.count()
    return lambda: BASE + timedelta(hours=step_hours * next(counter))


def test_backfills_active_document_missing_a_feature() -> None:
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    proc = FakeProcessor()
    processed = FeatureReconciler(repo, [proc], ["t1"], clock=lambda: BASE).reconcile()

    assert processed == 1
    assert proc.calls == ["d1"]
    rows = repo.list_for_document("t1", "d1")
    assert len(rows) == 1
    assert rows[0].feature == "demo" and rows[0].status is FeatureStatus.DONE


def test_retries_then_gives_up_recording_the_error() -> None:
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    proc = FakeProcessor(fail_times=99)  # always fails
    rec = FeatureReconciler(repo, [proc], ["t1"], backoff_base_seconds=1, clock=_advancing_clock())
    for _ in range(6):  # more passes than max_attempts
        rec.reconcile()

    row = repo.list_for_document("t1", "d1")[0]
    assert row.status is FeatureStatus.FAILED
    assert row.attempts == row.max_attempts == 3  # stopped after max attempts
    assert len(proc.calls) == 3
    assert row.last_error and "boom" in row.last_error


def test_version_bump_reprocesses_done_document() -> None:
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    repo.record_done("t1", "d1", "demo", 1)  # already done at v1

    class V2(FakeProcessor):
        version = 2

    proc = V2()
    processed = FeatureReconciler(repo, [proc], ["t1"], clock=lambda: BASE).reconcile()

    assert processed == 1
    assert proc.calls == ["d1"]
    row = repo.list_for_document("t1", "d1")[0]
    assert row.status is FeatureStatus.DONE and row.feature_version == 2


def test_reclaims_a_stuck_running_row() -> None:
    repo = InMemoryFeatureRepository()
    # A row left 'running' by a worker that died long ago (crash mid-processing).
    repo.rows.append(
        DocumentFeature(
            id="f1",
            tenant_id="t1",
            document_id="d1",
            feature="demo",
            status=FeatureStatus.RUNNING,
            attempts=1,
            last_attempt_at=BASE - timedelta(hours=2),
            created_at=BASE - timedelta(hours=2),
            updated_at=BASE - timedelta(hours=2),
        )
    )
    proc = FakeProcessor()
    processed = FeatureReconciler(
        repo, [proc], ["t1"], lease_seconds=900, clock=lambda: BASE
    ).reconcile()

    assert processed == 1
    assert proc.calls == ["d1"]
    assert repo.list_for_document("t1", "d1")[0].status is FeatureStatus.DONE
