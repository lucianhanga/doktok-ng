"""Readiness levels (#625, security audit F-13): shallow public probe, authenticated deep probe,
cached. The old /ready fanned out unauthenticated to DB + Ollama + Gotenberg (+ OpenAI) on EVERY
call - a cheap flood pinned the threadpool - and leaked internal addresses in failure details.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from doktok_api.main import create_app
from doktok_contracts.ports import AppSettingsRepository
from doktok_core.config import Settings
from doktok_core.registry import build_registry
from doktok_core.settings.inmemory import InMemoryAppSettingsRepository
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer tok-a"}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("DOKTOK_"):
            monkeypatch.delenv(key, raising=False)


class _FakeConn:
    def execute(self, _sql: str) -> None:
        return None


class _FakeDb:
    def __init__(self, *, ok: bool) -> None:
        self._ok = ok

    def connection(self) -> object:
        ok = self._ok

        class _Ctx:
            def __enter__(self) -> _FakeConn:
                if not ok:
                    raise RuntimeError("fake-db: connection refused from 10.9.8.7:59999")
                return _FakeConn()

            def __exit__(self, *args: object) -> None:
                return None

        return _Ctx()


def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, db_ok: bool
) -> tuple[TestClient, list[str]]:
    registry = build_registry()
    registry.register(AppSettingsRepository, InMemoryAppSettingsRepository())  # type: ignore[type-abstract]
    settings = Settings(  # type: ignore[call-arg]
        env="test", tenant_tokens={"tok-a": "t"}, files_root=str(tmp_path), _env_file=None
    )
    app = create_app(settings=settings, registry=registry)
    from doktok_api import dependencies

    monkeypatch.setattr(dependencies, "_get_database", lambda request: _FakeDb(ok=db_ok))
    import httpx as httpx_mod

    urls: list[str] = []

    def _fake_get(url: str, **kwargs: object) -> object:
        urls.append(url)

        class _Resp:
            status_code = 200

            def json(self) -> dict[str, object]:
                return {"models": []}

        return _Resp()

    monkeypatch.setattr(httpx_mod, "get", _fake_get)
    return TestClient(app), urls


def test_shallow_ready_is_public_and_probes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, urls = _client(tmp_path, monkeypatch, db_ok=True)
    resp = client.get("/ready")  # no credential - stays public
    assert resp.status_code == 200
    checks = resp.json()["checks"]
    assert [c["name"] for c in checks] == ["database"]  # no Ollama/Gotenberg/OpenAI fan-out
    assert urls == []  # zero outbound HTTP probes


def test_shallow_ready_failure_detail_is_static(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, db_ok=False)
    resp = client.get("/ready")
    assert resp.status_code == 503
    check = resp.json()["checks"][0]
    assert check["detail"] == "unavailable"
    assert "10.9.8.7" not in resp.text and "59999" not in resp.text  # no internal addresses


def test_deep_ready_requires_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(tmp_path, monkeypatch, db_ok=True)
    assert client.get("/ready?deep=1").status_code == 401


def test_deep_ready_with_token_probes_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, urls = _client(tmp_path, monkeypatch, db_ok=True)
    resp = client.get("/ready?deep=1", headers=AUTH)
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["checks"]]
    assert "ollama" in names and "gotenberg" in names and "database" in names
    assert any(url.endswith("/api/tags") for url in urls)


def test_ready_responses_are_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, urls = _client(tmp_path, monkeypatch, db_ok=True)
    first = client.get("/ready?deep=1", headers=AUTH)
    assert first.status_code == 200
    probed = len(urls)
    assert probed > 0
    second = client.get("/ready?deep=1", headers=AUTH)
    assert second.status_code == 200
    assert len(urls) == probed  # the second call was served from the cache
