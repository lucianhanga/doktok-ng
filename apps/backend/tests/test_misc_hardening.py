"""Misc hardening bundle (#654, security audit F-42).

- The caller-supplied X-Request-ID is echoed into responses and the log contextvar: it is now
  length-capped and charset-restricted (the HTTP parser blocks CR/LF; this stops log forging
  with printable junk and unbounded log bloat).
- The maintenance sentinel no longer fails OPEN on a stat error after the flag was recently
  seen: a broken mount mid-restore must not let mutations proceed against a half-restored DB.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import _maintenance_active, create_app
from doktok_core.config import Settings
from fastapi.testclient import TestClient
from starlette.datastructures import State


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


def _client() -> TestClient:
    settings = Settings(env="test", tenant_tokens={"tok-a": "tenant-a"}, _env_file=None)  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings))


def test_normal_request_id_is_echoed() -> None:
    resp = _client().get("/health", headers={"X-Request-ID": "req-123_ABC.def"})
    assert resp.headers["x-request-id"] == "req-123_ABC.def"


def test_overlong_request_id_is_replaced() -> None:
    resp = _client().get("/health", headers={"X-Request-ID": "x" * 500})
    echoed = resp.headers["x-request-id"]
    assert echoed != "x" * 500
    assert len(echoed) == 32  # a freshly minted uuid4 hex


def test_unsafe_charset_request_id_is_replaced() -> None:
    # Printable junk (spaces, braces, quotes) must not reach the log contextvar either.
    resp = _client().get("/health", headers={"X-Request-ID": '{"injected": "json"}'})
    echoed = resp.headers["x-request-id"]
    assert "{" not in echoed and '"' not in echoed and " " not in echoed


# --- maintenance sentinel: fail closed after a recently-seen flag (F-42) ---


def _settings(backup_dir: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="test",
        tenant_tokens={"tok-a": "tenant-a"},
        backup_dir=str(backup_dir),
        _env_file=None,
    )


def _touch_flag(backup_dir: Path) -> Path:
    flag = backup_dir / "status" / "maintenance.flag"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    return flag


def test_maintenance_stat_error_fails_closed_after_a_seen_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    state = State()
    flag = _touch_flag(tmp_path)
    assert _maintenance_active(settings, state) is True
    flag.unlink()
    assert _maintenance_active(settings, state) is False
    # The mount breaks AFTER the flag was seen: exists() now raises, and within the recency
    # window the gate stays CLOSED (a restore may be mid-apply on the other side of that mount).
    monkeypatch.setattr(Path, "exists", lambda self: (_ for _ in ()).throw(OSError("mount gone")))
    assert _maintenance_active(settings, state) is True


def test_maintenance_stat_error_fails_open_when_never_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pre-existing fail-open baseline is kept when the flag was never observed.
    monkeypatch.setattr(Path, "exists", lambda self: (_ for _ in ()).throw(OSError("mount gone")))
    assert _maintenance_active(_settings(tmp_path), State()) is False
