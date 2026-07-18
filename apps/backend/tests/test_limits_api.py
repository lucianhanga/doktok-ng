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


def test_chunked_body_is_rejected_with_411_pre_auth() -> None:
    # F-05: a chunked body carries no Content-Length, bypassed the 413 check, and was fully
    # buffered in RAM - even pre-auth on /auth/login. An iterator body makes httpx send
    # Transfer-Encoding: chunked (no Content-Length).
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", content=iter([b'{"tenant_id": "t"}', b"{}"]))
    assert resp.status_code == 411


def test_chunked_upload_is_rejected_with_411() -> None:
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    resp = client.post("/api/v1/ingestion/upload", content=iter([b"x" * 64]))
    assert resp.status_code == 411


def test_garbage_content_length_is_rejected_with_400() -> None:
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", content=b"{}", headers={"Content-Length": "abc"})
    assert resp.status_code == 400


def test_giant_numeric_content_length_is_413_not_500() -> None:
    # F-27: a numeric Content-Length beyond Python's int-string cap used to crash the middleware.
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", content=b"{}", headers={"Content-Length": "9" * 5000})
    assert resp.status_code == 413


def test_restore_preview_is_exempt_from_length_requirement() -> None:
    # The preview streams the upload with its own byte-counted cap, so it may arrive chunked.
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    resp = client.post("/api/v1/settings/backup/restore/preview", content=iter([b"x" * 64]))
    assert resp.status_code != 411


def test_normal_json_post_and_reads_still_pass() -> None:
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    # A normal JSON POST (proper Content-Length) is not confused for the attack shape.
    assert client.post("/nope", json={"a": 1}).status_code != 411
    assert client.get("/nope").status_code == 404  # GET has no Content-Length and passes
    assert client.options("/nope").status_code != 411  # CORS preflight carries no body


def test_bodyless_methods_pass() -> None:
    # No Transfer-Encoding and no Content-Length means NO body (RFC 9112): DELETEs and body-less
    # POSTs are not the chunked-attack shape and must not be confused for it.
    app = create_app(settings=Settings(env="test", tenant_tokens={"tok-a": "t"}))
    client = TestClient(app)
    assert client.delete("/nope").status_code != 411
    assert client.post("/nope").status_code != 411
