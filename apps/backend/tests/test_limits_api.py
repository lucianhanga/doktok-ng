"""Request body-size limit (APP-10) and per-token rate limiting (APP-9)."""

from __future__ import annotations

from doktok_api.main import create_app
from doktok_core.config import Settings
from fastapi.testclient import TestClient


def test_oversized_body_is_rejected_with_413() -> None:
    app = create_app(settings=Settings(env="test", max_request_mb=1))
    client = TestClient(app)
    # Just over the 1 MB cap; the check happens before routing so the path need not exist.
    resp = client.post("/nope", content=b"x" * (1024 * 1024 + 16))
    assert resp.status_code == 413


def test_rate_limit_returns_429_with_retry_after() -> None:
    app = create_app(settings=Settings(env="test", rate_limit_per_minute=2))
    client = TestClient(app)
    headers = {"Authorization": "Bearer tok-a"}
    # Bucket capacity 2: first two pass through (routed -> 404), the third is rate-limited.
    assert client.get("/nope", headers=headers).status_code == 404
    assert client.get("/nope", headers=headers).status_code == 404
    third = client.get("/nope", headers=headers)
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1


def test_rate_limit_disabled_by_default() -> None:
    app = create_app(settings=Settings(env="test"))  # rate_limit_per_minute=0
    client = TestClient(app)
    headers = {"Authorization": "Bearer tok-a"}
    for _ in range(5):
        assert client.get("/nope", headers=headers).status_code == 404  # never throttled


def test_health_exempt_from_rate_limit() -> None:
    app = create_app(settings=Settings(env="test", rate_limit_per_minute=1))
    client = TestClient(app)
    headers = {"Authorization": "Bearer tok-a"}
    for _ in range(3):
        assert client.get("/health", headers=headers).status_code == 200  # exempt
