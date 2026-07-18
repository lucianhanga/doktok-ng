"""RateLimiter bounded state (F-06): idle-bucket eviction + a hard cap with fail-closed 429.

Before this, the bucket map grew forever on attacker-controlled keys (each unique login email or
bearer token was a permanent entry), turning the limiter itself into a memory-exhaustion vector.
"""

from __future__ import annotations

from doktok_api.ratelimit import RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_idle_buckets_are_evicted_on_sweep() -> None:
    clock = _Clock()
    limiter = RateLimiter(60, clock=clock)  # refill window = 60s
    for i in range(10):
        limiter.allow(f"key-{i}")
    assert len(limiter._buckets) == 10
    clock.now += 3600  # every bucket idle far past its refill window
    for _ in range(1024):  # cross the amortized sweep cadence
        limiter.allow("pinger")
    assert set(limiter._buckets) == {"pinger"}


def test_active_buckets_survive_the_sweep() -> None:
    clock = _Clock()
    limiter = RateLimiter(60, clock=clock)
    limiter.allow("busy")
    clock.now += 30  # inside the refill window
    for _ in range(1024):
        limiter.allow("pinger")
    assert "busy" in limiter._buckets


def test_hard_cap_refuses_new_keys_with_shared_429() -> None:
    clock = _Clock()
    limiter = RateLimiter(60, clock=clock, max_buckets=2)
    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True
    allowed, retry_after = limiter.allow("c")  # a NEW key beyond the cap
    assert allowed is False
    assert retry_after > 0
    # Existing keys keep working (they refill and are not evicted while active).
    clock.now += 60
    assert limiter.allow("a")[0] is True


def test_bucket_semantics_unchanged() -> None:
    clock = _Clock()
    limiter = RateLimiter(2, clock=clock)
    assert limiter.allow("k") == (True, 0)
    assert limiter.allow("k") == (True, 0)
    allowed, retry_after = limiter.allow("k")
    assert allowed is False and retry_after >= 1
