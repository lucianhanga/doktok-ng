from doktok_core.ingestion.stability import FileObservation, StabilityTracker


def obs(size: int, mtime: float, path: str = "/ingest/a.txt") -> FileObservation:
    return FileObservation(path=path, size=size, mtime=mtime)


def test_first_observation_is_never_stable() -> None:
    tracker = StabilityTracker(stability_seconds=3)
    assert tracker.is_stable(obs(10, 100.0), now=0.0) is False


def test_becomes_stable_after_threshold_when_unchanged() -> None:
    tracker = StabilityTracker(stability_seconds=3)
    tracker.is_stable(obs(10, 100.0), now=0.0)
    assert tracker.is_stable(obs(10, 100.0), now=2.0) is False
    assert tracker.is_stable(obs(10, 100.0), now=3.0) is True


def test_change_resets_the_timer() -> None:
    tracker = StabilityTracker(stability_seconds=3)
    tracker.is_stable(obs(10, 100.0), now=0.0)
    # File grew at t=2 -> timer resets.
    assert tracker.is_stable(obs(20, 105.0), now=2.0) is False
    assert tracker.is_stable(obs(20, 105.0), now=4.0) is False
    assert tracker.is_stable(obs(20, 105.0), now=5.0) is True


def test_forget_removes_tracking() -> None:
    tracker = StabilityTracker(stability_seconds=1)
    tracker.is_stable(obs(10, 100.0), now=0.0)
    tracker.forget("/ingest/a.txt")
    # After forgetting, the next observation starts the timer over.
    assert tracker.is_stable(obs(10, 100.0), now=10.0) is False
