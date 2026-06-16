"""In-process per-token rate limiter (APP-9).

A token bucket per tenant bearer token: capacity = the configured requests/minute (the burst),
refilled continuously at that rate. Sufficient for a single-box limited-production deployment; for
multiple API replicas a shared store (Redis) would be needed instead.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int, *, clock: object = time.monotonic) -> None:
        self._capacity = float(per_minute)
        self._refill_per_sec = per_minute / 60.0
        self._clock = clock  # callable[[], float]
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Try to consume one token for ``key``. Returns (allowed, retry_after_seconds)."""
        now = self._clock()  # type: ignore[operator]
        with self._lock:
            tokens, last = self._buckets.get(key, (self._capacity, now))
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
