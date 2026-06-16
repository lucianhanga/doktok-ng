"""/metrics endpoint (APP-13): token-gated Prometheus exposition."""

from __future__ import annotations

from doktok_api.main import create_app
from doktok_core.config import Settings
from fastapi.testclient import TestClient

TOKENS = {"tok-a": "tenant-a"}
AUTH = {"Authorization": "Bearer tok-a"}


def _client() -> TestClient:
    return TestClient(create_app(settings=Settings(env="test", tenant_tokens=TOKENS)))


def test_metrics_requires_auth() -> None:
    assert _client().get("/metrics").status_code == 401


def test_metrics_exposes_counters_and_uptime() -> None:
    client = _client()
    client.get("/health")  # generate a request to count
    body = client.get("/metrics", headers=AUTH).text
    assert "doktok_requests_total" in body
    assert "doktok_request_latency_seconds_count" in body
    assert "doktok_uptime_seconds" in body
