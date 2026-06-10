"""Stable-file detection (ADR-0004).

A dropped file is only safe to ingest once it has stopped changing (the producer finished writing).
A file is considered stable when its size and modification time have been unchanged for at least
``stability_seconds``. This tracker is pure and clock-injectable so it can be unit-tested without
sleeps or a real filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileObservation:
    path: str
    size: int
    mtime: float


class StabilityTracker:
    def __init__(self, stability_seconds: float) -> None:
        self._stability_seconds = stability_seconds
        # path -> (size, mtime, first_seen_at_for_this_signature)
        self._seen: dict[str, tuple[int, float, float]] = {}

    def is_stable(self, observation: FileObservation, now: float) -> bool:
        """Record an observation and report whether the file is now stable."""
        prev = self._seen.get(observation.path)
        signature = (observation.size, observation.mtime)
        if prev is None or (prev[0], prev[1]) != signature:
            self._seen[observation.path] = (observation.size, observation.mtime, now)
            return False
        first_seen = prev[2]
        return (now - first_seen) >= self._stability_seconds

    def forget(self, path: str) -> None:
        """Stop tracking a path (e.g. after it has been ingested or removed)."""
        self._seen.pop(path, None)
