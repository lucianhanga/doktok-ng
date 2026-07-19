"""In-memory TenantRegistry unit semantics (#554) - small, DB-free mirrors of the Postgres repo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import Invitation, Tenant, User
from doktok_core.security.auth import hash_token
from doktok_core.security.inmemory import InMemoryTenantRegistry


def _registry_with_invite() -> InMemoryTenantRegistry:
    reg = InMemoryTenantRegistry()
    reg.create_tenant(Tenant(id="t1", name="T1"))
    reg.create_user(
        User(id="u1", tenant_id="t1", email="inv@x.com", role="editor", status="invited")
    )
    reg.create_invitation(
        Invitation(
            id="inv1",
            tenant_id="t1",
            user_id="u1",
            email="inv@x.com",
            role="editor",
            token_sha256=hash_token("invite-token"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    return reg


def test_accept_invitation_is_single_claim_and_sets_credentials() -> None:
    # F-36 (#648): claim + password-set + activation happen as one unit; a second claim - e.g. a
    # concurrent request that also passed the pre-check - loses and reports False.
    reg = _registry_with_invite()
    assert reg.accept_invitation("t1", "u1", "inv1", "scrypt$new") is True
    user = reg.get_user("t1", "u1")
    assert user is not None and user.status == "active"
    assert reg.users["u1"].password_hash == "scrypt$new"
    assert reg.accept_invitation("t1", "u1", "inv1", "scrypt$other") is False
    assert reg.users["u1"].password_hash == "scrypt$new"  # the losing claim changed nothing
