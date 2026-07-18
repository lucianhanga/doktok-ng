from doktok_core.dev.seed import DEV_TENANT_ID, DEV_USERS, seed_dev, seed_guard
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.passwords import verify_password


def test_seed_guard_allows_local_loopback() -> None:
    assert seed_guard("local", "127.0.0.1", allow_remote=False) is None
    assert seed_guard("dev", "localhost", allow_remote=False) is None


def test_seed_guard_refuses_non_dev_env() -> None:
    assert seed_guard("prod", "127.0.0.1", allow_remote=False) is not None
    assert (
        seed_guard("production", "localhost", allow_remote=True) is not None
    )  # env gate is absolute


def test_seed_guard_refuses_remote_db_without_override() -> None:
    assert seed_guard("local", "db.prod.internal", allow_remote=False) is not None
    assert seed_guard("local", "db.prod.internal", allow_remote=True) is None


FIXED = "seed-password-123"  # pragma: allowlist secret (>= 12)


def _pw(_email: str) -> str:
    return FIXED


def test_seed_creates_tenant_and_one_user_per_role() -> None:
    reg = InMemoryTenantRegistry()
    accounts = seed_dev(reg, password_for=_pw)

    assert reg.get_tenant(DEV_TENANT_ID) is not None
    assert {(a.email, a.role) for a in accounts} == set(DEV_USERS)
    assert all(a.created and a.password_set for a in accounts)

    for email, role in DEV_USERS:
        user = reg.get_user_by_email(DEV_TENANT_ID, email)
        assert user is not None
        assert user.role == role
        assert user.status == "active"
        # The seeded account can actually log in with the returned password.
        assert verify_password(FIXED, user.password_hash)


def test_seed_is_idempotent_without_reset() -> None:
    reg = InMemoryTenantRegistry()
    seed_dev(reg, password_for=_pw)
    again = seed_dev(
        reg, password_for=lambda _e: "different-password-xyz"
    )  # pragma: allowlist secret

    # No new users, and existing passwords are left untouched.
    assert len(reg.users) == len(DEV_USERS)
    assert all(not a.created and not a.password_set and a.password is None for a in again)
    admin = reg.get_user_by_email(DEV_TENANT_ID, "dev-admin@doktok.local")
    assert admin is not None and verify_password(FIXED, admin.password_hash)


def test_reset_rotates_existing_passwords() -> None:
    reg = InMemoryTenantRegistry()
    seed_dev(reg, password_for=_pw)
    new_pw = "rotated-password-99"  # pragma: allowlist secret
    again = seed_dev(reg, password_for=lambda _e: new_pw, reset=True)

    assert all(a.password_set and not a.created for a in again)
    admin = reg.get_user_by_email(DEV_TENANT_ID, "dev-admin@doktok.local")
    assert admin is not None
    assert verify_password(new_pw, admin.password_hash)
    assert not verify_password(FIXED, admin.password_hash)  # old password no longer works


def test_seeded_admin_is_platform_admin() -> None:
    # The dev admin doubles as the platform-owner persona for UI login (#613, ADR-0025); the
    # editor/viewer stay non-platform so both denial paths are exercisable.
    reg = InMemoryTenantRegistry()
    seed_dev(reg, password_for=_pw)
    flags = {
        email: reg.get_user_by_email(DEV_TENANT_ID, email).is_platform_admin  # type: ignore[union-attr]
        for email, _role in DEV_USERS
    }
    assert flags == {
        "dev-admin@doktok.local": True,
        "dev-manager@doktok.local": False,
        "dev-editor@doktok.local": False,
        "dev-viewer@doktok.local": False,
    }


def test_reset_resyncs_the_platform_flag() -> None:
    reg = InMemoryTenantRegistry()
    seed_dev(reg, password_for=_pw)
    admin = reg.get_user_by_email(DEV_TENANT_ID, "dev-admin@doktok.local")
    assert admin is not None
    reg.set_platform_admin(DEV_TENANT_ID, admin.id, False)  # revoked out-of-band
    seed_dev(reg, password_for=_pw, reset=True)
    admin = reg.get_user_by_email(DEV_TENANT_ID, "dev-admin@doktok.local")
    assert admin is not None and admin.is_platform_admin is True
