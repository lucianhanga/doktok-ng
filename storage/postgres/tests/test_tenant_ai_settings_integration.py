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
