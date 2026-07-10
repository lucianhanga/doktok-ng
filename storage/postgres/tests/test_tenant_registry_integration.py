"""Integration tests for the Postgres tenant/user/api-token registry (#554, test* tenants only)."""

from __future__ import annotations

from doktok_contracts.schemas import ApiToken, Tenant, TokenResolution, User
from doktok_core.security.auth import hash_token
from doktok_storage_postgres import Database, PostgresTenantRegistry

TENANT = "test-reg"


def test_create_and_resolve_token(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(User(id="u1", tenant_id=TENANT, email="a@b.com", display_name="A"))
    reg.create_api_token(
        ApiToken(
            id="tok1",
            tenant_id=TENANT,
            user_id="u1",
            token_sha256=hash_token("plaintext-secret"),
            token_prefix="plai",
            name="test token",
        )
    )

    assert reg.get_tenant(TENANT) is not None
    assert reg.get_user(TENANT, "u1") is not None
    assert reg.resolve_token(hash_token("plaintext-secret")) == TokenResolution(
        tenant_id=TENANT, user_id="u1"
    )
    # A wrong hash never resolves.
    assert reg.resolve_token(hash_token("wrong")) is None


def test_revoked_token_stops_resolving(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_api_token(
        ApiToken(
            id="tok2",
            tenant_id=TENANT,
            token_sha256=hash_token("to-be-revoked"),
        )
    )
    assert reg.resolve_token(hash_token("to-be-revoked")) is not None

    reg.revoke_api_token(TENANT, "tok2")
    assert reg.resolve_token(hash_token("to-be-revoked")) is None


# Opaque digest fixtures (not real scrypt output - the registry stores whatever string it is given).
_STORED_HASH = "scrypt$stored"  # pragma: allowlist secret
_ROTATED_HASH = "scrypt$rotated"  # pragma: allowlist secret


def test_password_lookup_and_read_path_hides_hash(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(
        User(id="u9", tenant_id=TENANT, email="Login@Example.com", password_hash=_STORED_HASH)
    )

    # get_user_by_email returns the credential digest and matches case-insensitively.
    by_email = reg.get_user_by_email(TENANT, "login@example.com")
    assert by_email is not None
    assert by_email.id == "u9"
    assert by_email.password_hash == _STORED_HASH

    # The plain read path never surfaces the digest.
    plain = reg.get_user(TENANT, "u9")
    assert plain is not None
    assert plain.password_hash is None

    # set_user_password replaces it.
    reg.set_user_password(TENANT, "u9", _ROTATED_HASH)
    rotated = reg.get_user_by_email(TENANT, "login@example.com")
    assert rotated is not None
    assert rotated.password_hash == _ROTATED_HASH

    # Unknown email -> None.
    assert reg.get_user_by_email(TENANT, "nobody@example.com") is None


def test_role_defaults_and_assignment(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    # A user created without an explicit role defaults to least privilege (viewer).
    reg.create_user(User(id="ur", tenant_id=TENANT, email="role@example.com"))
    created = reg.get_user(TENANT, "ur")
    assert created is not None
    assert created.role == "viewer"

    reg.set_user_role(TENANT, "ur", "admin")
    assert reg.get_user(TENANT, "ur").role == "admin"  # type: ignore[union-attr]


def test_tenant_scoped_token_has_no_user(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_api_token(
        ApiToken(id="tok3", tenant_id=TENANT, token_sha256=hash_token("tenant-only"))
    )
    resolution = reg.resolve_token(hash_token("tenant-only"))
    assert resolution is not None
    assert resolution.tenant_id == TENANT
    assert resolution.user_id is None
