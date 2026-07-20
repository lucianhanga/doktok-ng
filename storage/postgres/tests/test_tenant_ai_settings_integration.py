"""Integration tests for the Postgres tenant_ai_settings store (epic #708, T1; test* tenants)."""

from __future__ import annotations

from doktok_contracts.schemas import AiPurposeSettings, Tenant, TenantAiSettings
from doktok_storage_postgres import Database, PostgresAppSettingsRepository, PostgresTenantRegistry


def test_tenant_ai_settings_round_trip_and_delete(db: Database) -> None:
    PostgresTenantRegistry(db).create_tenant(Tenant(id="test-tai", name="TAI"))
    repo = PostgresAppSettingsRepository(db)
    assert repo.get_tenant_ai_settings("test-tai") is None

    override = TenantAiSettings(
        pipeline=AiPurposeSettings(provider="openai", model="gpt-4o-mini", num_ctx=8192),
        no_egress=False,
    )
    repo.set_tenant_ai_settings("test-tai", override)
    loaded = repo.get_tenant_ai_settings("test-tai")
    assert loaded is not None
    assert loaded.pipeline is not None and loaded.pipeline.model == "gpt-4o-mini"
    assert loaded.no_egress is False
    assert loaded.rag is None  # partial: unset purposes stay unset

    # Replace wholesale: the pipeline override disappears when not re-sent.
    repo.set_tenant_ai_settings("test-tai", TenantAiSettings(no_egress=True))
    loaded = repo.get_tenant_ai_settings("test-tai")
    assert loaded is not None and loaded.pipeline is None and loaded.no_egress is True

    # Tenant isolation + delete.
    assert repo.get_tenant_ai_settings("test-other") is None
    repo.delete_tenant_ai_settings("test-tai")
    assert repo.get_tenant_ai_settings("test-tai") is None


def test_tenant_openai_key_round_trip_encrypted_and_deleted(db: Database) -> None:
    PostgresTenantRegistry(db).create_tenant(Tenant(id="test-tak", name="TAK"))
    repo = PostgresAppSettingsRepository(db, secrets_key="test-master-key")
    assert repo.get_tenant_openai_api_key("test-tak") == ""

    repo.set_tenant_openai_api_key("test-tak", "sk-tenant-secret")
    assert repo.get_tenant_openai_api_key("test-tak") == "sk-tenant-secret"
    # Encrypted at rest (APP-8): the raw app_settings value is not the plaintext key.
    with db.connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=%s", ("tenant:test-tak:openai_api_key",)
        ).fetchone()
    assert row is not None and "sk-tenant-secret" not in str(row[0])

    # Tenant isolation + resetting the override drops the key too.
    assert repo.get_tenant_openai_api_key("test-other") == ""
    repo.delete_tenant_ai_settings("test-tak")
    assert repo.get_tenant_openai_api_key("test-tak") == ""
