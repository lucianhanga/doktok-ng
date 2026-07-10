"""Integration tests for the Postgres per-user preference store (#558, test* tenants only)."""

from __future__ import annotations

from doktok_storage_postgres import Database, PostgresUserPreferenceRepository

TENANT = "test-prefs"


def test_upsert_merge_and_read(db: Database) -> None:
    repo = PostgresUserPreferenceRepository(db)
    assert repo.get_all(TENANT, "u1") == {}

    repo.set_many(TENANT, "u1", {"docLayout": "grid", "thumbSize": 3})
    repo.set_many(TENANT, "u1", {"chat": {"mode": "agentic"}, "thumbSize": 5})  # overwrite one
    assert repo.get_all(TENANT, "u1") == {
        "docLayout": "grid",
        "thumbSize": 5,
        "chat": {"mode": "agentic"},
    }


def test_scoped_by_subject(db: Database) -> None:
    repo = PostgresUserPreferenceRepository(db)
    repo.set_many(TENANT, "u1", {"a": 1})
    repo.set_many(TENANT, "u2", {"a": 2})
    assert repo.get_all(TENANT, "u1") == {"a": 1}
    assert repo.get_all(TENANT, "u2") == {"a": 2}


def test_delete_is_idempotent(db: Database) -> None:
    repo = PostgresUserPreferenceRepository(db)
    repo.set_many(TENANT, "u1", {"a": 1, "b": 2})
    repo.delete(TENANT, "u1", "a")
    repo.delete(TENANT, "u1", "missing")  # no-op
    assert repo.get_all(TENANT, "u1") == {"b": 2}


def test_set_many_empty_is_noop(db: Database) -> None:
    repo = PostgresUserPreferenceRepository(db)
    repo.set_many(TENANT, "u1", {})
    assert repo.get_all(TENANT, "u1") == {}
