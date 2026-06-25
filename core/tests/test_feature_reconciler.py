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
    rows = {r.feature: r for r in repo.list_for_document("t1", "d1")}
    assert rows["demo"].status is FeatureStatus.DONE
    # The 'extract' marker (the 'text' badge) self-heals: an active document always gets a done
    # extract row even though it has no reconciler processor.
    assert rows["extract"].status is FeatureStatus.DONE


class _Stage:
    """A processor with declared prerequisites, for stage-dependency tests."""

    version = 1

    def __init__(
        self, name: str, *, dependencies: tuple[str, ...] = (), fail_times: int = 0
    ) -> None:
        self.name = name
        self.dependencies = dependencies
        self.calls: list[str] = []
        self._fail_times = fail_times

    def process(self, tenant_id: str, document_id: str) -> None:  # noqa: ARG002
        self.calls.append(document_id)
        if len(self.calls) <= self._fail_times:
            raise RuntimeError("boom")


def test_dependent_stage_is_blocked_until_its_prerequisite_succeeds() -> None:
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    child = _Stage("child", dependencies=("root",))
    root = _Stage("root", fail_times=99)  # never reaches 'done'
    rec = FeatureReconciler(
        repo, [child, root], ["t1"], backoff_base_seconds=0.0, clock=_advancing_clock()
    )
    for _ in range(6):
        rec.reconcile()

    assert child.calls == []  # child never ran - its prerequisite never succeeded
    rows = {r.feature: r.status for r in repo.list_for_document("t1", "d1")}
    assert rows["root"] is FeatureStatus.FAILED
    assert rows["child"] is FeatureStatus.PENDING  # still waiting on its input


def test_dependent_stage_runs_once_its_prerequisite_is_done() -> None:
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    child = _Stage("child", dependencies=("root",))
    root = _Stage("root")
    # One pass drains both: root is claimed first (child gated), then child becomes claimable.
    FeatureReconciler(repo, [child, root], ["t1"], clock=lambda: BASE).reconcile()

    assert root.calls == ["d1"] and child.calls == ["d1"]
    rows = {r.feature: r.status for r in repo.list_for_document("t1", "d1")}
    assert rows["root"] is FeatureStatus.DONE and rows["child"] is FeatureStatus.DONE


def test_ensure_for_active_self_heals_a_missing_extract_marker() -> None:
    # A document activated by a path that skipped the inline 'extract' write has every processor
    # feature but no extract row, so the 'text' badge is missing. ensure_for_active backfills it.
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    repo.rows.append(
        DocumentFeature(
            id="x",
            tenant_id="t1",
            document_id="d1",
            feature="demo",
            feature_version=1,
            status=FeatureStatus.DONE,
            created_at=BASE,
            updated_at=BASE,
        )
    )

    repo.ensure_for_active("t1", [("demo", 1)])

    rows = {r.feature: r for r in repo.list_for_document("t1", "d1")}
    assert rows["extract"].status is FeatureStatus.DONE
    assert rows["extract"].completed_at is not None
    # Idempotent: a second pass does not duplicate the marker.
    repo.ensure_for_active("t1", [("demo", 1)])
    extracts = [r for r in repo.list_for_document("t1", "d1") if r.feature == "extract"]
    assert len(extracts) == 1


def test_recover_running_requeues_orphaned_rows() -> None:
    # A prior worker claimed the row and was killed mid-run, leaving it 'running' under its lease.
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    repo.ensure_for_active("t1", [("demo", 1)])
    claimed = repo.claim_next("t1", now=BASE, reclaim_before=BASE - timedelta(hours=1))
    assert claimed is not None and claimed.status is FeatureStatus.RUNNING

    rec = FeatureReconciler(repo, [FakeProcessor()], ["t1"], clock=lambda: BASE)
    assert rec.recover_running() == 1  # startup recovery, no waiting out the lease
    assert repo.list_for_document("t1", "d1")[0].status is FeatureStatus.PENDING

    # The very next pass now drains it instead of stalling for the full lease window.
    assert rec.reconcile() == 1
    assert repo.list_for_document("t1", "d1")[0].status is FeatureStatus.DONE


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


def test_concurrent_drain_processes_every_row_exactly_once() -> None:
    """With concurrency>1, distinct rows are claimed per worker and all are processed once."""
    import threading

    rows = [
        DocumentFeature(
            id=f"f{i}",
            tenant_id="t1",
            document_id=f"d{i}",
            feature="demo",
            created_at=BASE,
            updated_at=BASE,
        )
        for i in range(12)
    ]
    lock = threading.Lock()
    cursor = iter(rows)
    done: list[str] = []

    class ThreadSafeRepo:
        def ensure_for_active(self, tenant_id, registered):  # type: ignore[no-untyped-def]
            return 0

        def claim_next(self, tenant_id, *, now, reclaim_before, dependencies=()):  # type: ignore[no-untyped-def]
            with lock:
                return next(cursor, None)

        def mark_done(self, feature_id, *, feature_version):  # type: ignore[no-untyped-def]
            with lock:
                done.append(feature_id)

        def mark_failed(self, feature_id, *, error, next_attempt_at):  # type: ignore[no-untyped-def]
            ...

    proc = FakeProcessor()
    processed = FeatureReconciler(
        ThreadSafeRepo(),  # type: ignore[arg-type]
        [proc],
        ["t1"],
        concurrency=4,
        clock=lambda: BASE,
    ).reconcile()

    assert processed == 12
    assert len(done) == 12 and set(done) == {f"f{i}" for i in range(12)}  # each row once
    assert len(proc.calls) == 12 and set(proc.calls) == {f"d{i}" for i in range(12)}


def test_set_processors_swaps_the_active_processor() -> None:
    # Live AI-settings reload (M13 #371): set_processors replaces the processor set used by later
    # passes (e.g. rebuilt enrichment clients) without recreating the reconciler.
    repo = InMemoryFeatureRepository(active={"t1": ["d1"]})
    old = FakeProcessor()
    rec = FeatureReconciler(repo, [old], ["t1"], clock=lambda: BASE)
    new = FakeProcessor()
    rec.set_processors([new])

    processed = rec.reconcile()

    assert processed == 1
    assert old.calls == []  # the replaced processor is no longer used
    assert new.calls == ["d1"]  # the swapped-in processor handled the feature
