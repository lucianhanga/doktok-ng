"""HTTP security headers (#636, security audit F-22).

Every API response now carries baseline hardening headers (nosniff, X-Frame-Options SAMEORIGIN,
Referrer-Policy no-referrer, and a deny-all CSP with ``frame-ancestors 'self'`` so the UI's
same-origin PDF preview iframe keeps working). HSTS is emitted ONLY when the request arrived over
HTTPS (edge-terminated TLS forwarded by Caddy) - plain-HTTP dev/test deployments stay HSTS-free.
"""

from __future__ import annotations

import os

import pytest
from doktok_api.main import create_app
from doktok_core.config import Settings
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client() -> TestClient:
    settings = Settings(env="test", tenant_tokens={"tok-a": "tenant-a"}, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings))


def test_baseline_headers_on_api_responses() -> None:
    resp = _client().get("/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "no-referrer"
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp


def test_headers_present_on_auth_rejections() -> None:
    # A 401 from the auth dependency still carries the baseline headers.
    resp = _client().get("/api/v1/documents")
    assert resp.status_code == 401
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_hsts_absent_over_plain_http() -> None:
    resp = _client().get("/health")
    assert "strict-transport-security" not in resp.headers


def test_hsts_present_when_forwarded_proto_is_https() -> None:
    resp = _client().get("/health", headers={"X-Forwarded-Proto": "https"})
    assert resp.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
