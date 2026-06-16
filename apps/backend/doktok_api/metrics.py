"""Minimal in-process metrics for the /metrics endpoint (APP-13).

Low-cardinality request counters (by method + status) and a latency summary, rendered in the
Prometheus text exposition format. Process-local (one API replica); a multi-replica deployment would
aggregate at the scraper. Gauges (worker heartbeat age, uptime) are passed in at render time.
"""

from __future__ import annotations

import threading
import time


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._latency_sum = 0.0
        self._latency_count = 0
        self._start = time.monotonic()

    def observe(self, method: str, status: int, duration_s: float) -> None:
        with self._lock:
            key = (method, str(status))
            self._requests[key] = self._requests.get(key, 0) + 1
            self._latency_sum += duration_s
            self._latency_count += 1

    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start

    def render(self, gauges: dict[str, float]) -> str:
        lines: list[str] = []
        with self._lock:
            lines.append("# TYPE doktok_requests_total counter")
            for (method, status), count in sorted(self._requests.items()):
                lines.append(
                    f'doktok_requests_total{{method="{method}",status="{status}"}} {count}'
                )
            lines.append("# TYPE doktok_request_latency_seconds summary")
            lines.append(f"doktok_request_latency_seconds_sum {self._latency_sum:.6f}")
            lines.append(f"doktok_request_latency_seconds_count {self._latency_count}")
        for name, value in gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"
