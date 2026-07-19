"""Integration tests for the Postgres tenant/user/api-token registry (#554, test* tenants only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import ApiToken, Invitation, Tenant, TokenResolution, User
from doktok_core.security.auth import hash_token
from doktok_storage_postgres import Database, PostgresTenantRegistry

TENANT = "test-reg"


def test_status_and_invitation_lifecycle(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(
        User(id="uinv", tenant_id=TENANT, email="inv@example.com", role="editor", status="invited")
    )

    # set_user_status flips the lifecycle state.
    reg.set_user_status(TENANT, "uinv", "active")
    assert reg.get_user(TENANT, "uinv").status == "active"  # type: ignore[union-attr]
    reg.set_user_status(TENANT, "uinv", "deactivated")
    assert reg.get_user(TENANT, "uinv").status == "deactivated"  # type: ignore[union-attr]

    # Invitation round-trips by token hash and marks accepted (single-use).
    reg.create_invitation(
        Invitation(
            id="inv1",
            tenant_id=TENANT,
            user_id="uinv",
            email="inv@example.com",
            role="editor",
            token_sha256=hash_token("invite-token"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    found = reg.get_invitation_by_token(hash_token("invite-token"))
    assert found is not None and found.user_id == "uinv" and found.accepted_at is None
    reg.mark_invitation_accepted("inv1")
    assert reg.get_invitation_by_token(hash_token("invite-token")).accepted_at is not None  # type: ignore[union-attr]
    assert reg.get_invitation_by_token(hash_token("nope")) is None


def test_accept_invitation_is_atomic(db: Database) -> None:
    # F-36 (#648): the conditional UPDATE ... accepted_at IS NULL gates exactly one claimer; the
    # password-set + activation ride in the same transaction.
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(
        User(id="uacc", tenant_id=TENANT, email="acc@example.com", role="editor", status="invited")
    )
    reg.create_invitation(
        Invitation(
            id="inv-acc",
            tenant_id=TENANT,
            user_id="uacc",
            email="acc@example.com",
            role="editor",
            token_sha256=hash_token("accept-token"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    assert reg.accept_invitation(TENANT, "uacc", "inv-acc", "scrypt$new") is True
    assert reg.get_user(TENANT, "uacc").status == "active"  # type: ignore[union-attr]
    # A concurrent claim of the same invitation loses: exactly one accept wins.
    assert reg.accept_invitation(TENANT, "uacc", "inv-acc", "scrypt$other") is False


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
        tenant_id=TENANT, user_id="u1", role="admin"
    )
    # A wrong hash never resolves.
    assert reg.resolve_token(hash_token("wrong")) is None


def test_resolve_token_returns_the_stored_role(db: Database) -> None:
    # F-33 (#645): the api_token row's role round-trips through resolution (viewer here).
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_api_token(
        ApiToken(
            id="tok-role",
            tenant_id=TENANT,
            token_sha256=hash_token("ro-secret"),
            role="viewer",
        )
    )
    resolution = reg.resolve_token(hash_token("ro-secret"))
    assert resolution is not None
    assert resolution.role == "viewer"
    listed = reg.list_api_tokens(TENANT)
    assert next(t for t in listed if t.id == "tok-role").role == "viewer"


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


def test_admin_listings(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(
        User(
            id="ul1", tenant_id=TENANT, email="bob@example.com", password_hash="scrypt$x"
        )  # pragma: allowlist secret
    )
    reg.create_user(User(id="ul2", tenant_id=TENANT, email="ann@example.com"))
    reg.create_api_token(
        ApiToken(
            id="lt1", tenant_id=TENANT, token_sha256=hash_token("list-tok"), token_prefix="list"
        )
    )

    # list_tenants includes ours.
    assert TENANT in {t.id for t in reg.list_tenants()}

    # list_users is ordered by email and never surfaces the password hash.
    users = reg.list_users(TENANT)
    assert [u.email for u in users] == ["ann@example.com", "bob@example.com"]
    assert all(u.password_hash is None for u in users)

    # list_api_tokens returns the tenant's tokens (prefix for display; secret never leaves here).
    tokens = reg.list_api_tokens(TENANT)
    assert [t.id for t in tokens] == ["lt1"]
    assert tokens[0].token_prefix == "list"


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


def test_platform_admin_flag_round_trip(db: Database) -> None:
    reg = PostgresTenantRegistry(db)
    reg.create_tenant(Tenant(id=TENANT, name="Test Registry Tenant"))
    reg.create_user(
        User(
            id="upa",
            tenant_id=TENANT,
            email="pa@example.com",
            role="admin",
            is_platform_admin=True,
        )
    )
    # create_user persists the flag; the plain read path surfaces it (without the digest).
    user = reg.get_user(TENANT, "upa")
    assert user is not None
    assert user.is_platform_admin is True
    assert user.password_hash is None

    # The flag defaults to off for a plain user; set_platform_admin toggles it.
    reg.create_user(User(id="uplain", tenant_id=TENANT, email="plain@example.com"))
    assert reg.get_user(TENANT, "uplain").is_platform_admin is False  # type: ignore[union-attr]
    reg.set_platform_admin(TENANT, "uplain", True)
    assert reg.get_user(TENANT, "uplain").is_platform_admin is True  # type: ignore[union-attr]
    # The login lookup and the listing carry the flag too.
    assert reg.get_user_by_email(TENANT, "plain@example.com").is_platform_admin is True  # type: ignore[union-attr]
    assert {u.id: u.is_platform_admin for u in reg.list_users(TENANT)}["uplain"] is True
    reg.set_platform_admin(TENANT, "uplain", False)
    assert reg.get_user(TENANT, "uplain").is_platform_admin is False  # type: ignore[union-attr]
