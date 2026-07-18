"""In-process per-token rate limiter (APP-9).

A token bucket per key (tenant bearer token, login account, login IP): capacity = the configured
requests/minute (the burst), refilled continuously at that rate. Sufficient for a single-box
limited-production deployment; for multiple API replicas a shared store (Redis) would be needed
instead.

Bounded state (F-06): bucket keys are attacker-controlled, so the map cannot grow forever. A
bucket idle past its full-refill window is indistinguishable from a fresh one and is dropped
(amortized sweep every SWEEP_EVERY calls). When the map still exceeds ``max_buckets`` with ACTIVE
buckets (a spray in progress), NEW keys get a shared 429 instead of another entry - fail closed
without growing memory; existing keys are unaffected.
"""

from __future__ import annotations

import threading
import time

_SWEEP_EVERY = 1024  # amortized eviction cadence (calls between passes)
_DEFAULT_MAX_BUCKETS = 50_000


class RateLimiter:
    def __init__(
        self,
        per_minute: int,
        *,
        clock: object = time.monotonic,
        max_buckets: int = _DEFAULT_MAX_BUCKETS,
    ) -> None:
        self._capacity = float(per_minute)
        self._refill_per_sec = per_minute / 60.0
        self._clock = clock  # callable[[], float]
        self._max_buckets = max_buckets
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)
        self._calls = 0
        self._lock = threading.Lock()

    def _full_refill_seconds(self) -> float:
        # Time for an empty bucket to refill completely; a bucket idle this long is as good as new.
        return self._capacity / self._refill_per_sec if self._refill_per_sec > 0 else 60.0

    def _sweep(self, now: float) -> None:
        idle = self._full_refill_seconds()
        stale = [key for key, (_, last) in self._buckets.items() if now - last >= idle]
        for key in stale:
            del self._buckets[key]

    def allow(self, key: str) -> tuple[bool, int]:
        """Try to consume one token for ``key``. Returns (allowed, retry_after_seconds)."""
        now = self._clock()  # type: ignore[operator]
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0:
                self._sweep(now)
            entry = self._buckets.get(key)
            if entry is None and len(self._buckets) >= self._max_buckets:
                # Fail closed (F-06): the map is full of ACTIVE buckets - a spray, not legitimate
                # traffic. Refuse the new key with a shared retry hint instead of growing memory.
                return False, 60
            tokens, last = entry or (self._capacity, now)
            tokens = min(self._capacity, tokens + (now - last) * self._refill_per_sec)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True, 0
            self._buckets[key] = (tokens, now)
            # Seconds until one token is available again.
            retry_after = (
                1 if self._refill_per_sec <= 0 else int((1.0 - tokens) / self._refill_per_sec) + 1
            )
            return False, retry_after
