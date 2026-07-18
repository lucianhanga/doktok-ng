"""The platform-owner tier (#613, security audit F-01): the identity attribute and how the
credential-resolution tiers populate it.

Platform admin is a deployment-level property of the authenticated identity (ADR-0025):
host-provisioned static tokens are platform admins; DB-minted user-less tokens are NOT (any tenant
admin can mint those); user-bound credentials (session JWT / user api token) inherit the user's
``is_platform_admin`` flag. The ``via`` marker on ``TokenResolution`` records WHICH tier resolved a
credential so the API can tell these apart.
"""

from __future__ import annotations

from doktok_contracts.schemas import ApiToken, TenantContext, TokenResolution, User
from doktok_core.security.auth import hash_token, resolve_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import decode_access_token, issue_access_token

_SECRET = "s" * 32  # test-only signing secret


def _user(user_id: str = "u1", **kwargs: object) -> User:
    return User(id=user_id, tenant_id="t1", email=f"{user_id}@example.com", **kwargs)  # type: ignore[arg-type]


def test_user_is_not_platform_admin_by_default() -> None:
    assert _user().is_platform_admin is False


def test_tenant_context_is_not_platform_by_default() -> None:
    assert TenantContext(tenant_id="t1").platform_admin is False


def test_resolve_token_marks_db_tier() -> None:
    registry = InMemoryTenantRegistry()
    registry.create_api_token(
        ApiToken(
            id="tok1",
            tenant_id="t1",
            user_id=None,
            token_sha256=hash_token("plaintext-token"),
        )
    )
    resolution = resolve_token("plaintext-token", registry=registry, static_tokens=None)
    assert resolution is not None
    assert resolution.via == "db"


def test_resolve_token_marks_static_tier() -> None:
    resolution = resolve_token("dev-token", registry=None, static_tokens={"dev-token": "t1"})
    assert resolution is not None
    assert resolution.via == "static"


def test_decode_access_token_marks_jwt_tier() -> None:
    token = issue_access_token(tenant_id="t1", user_id="u1", secret=_SECRET, ttl_seconds=60)
    resolution = decode_access_token(token, secret=_SECRET)
    assert resolution is not None
    assert resolution.via == "jwt"


def test_token_resolution_via_defaults_to_db() -> None:
    # Existing constructors (tests, fixtures, adapters) keep working unchanged.
    assert TokenResolution(tenant_id="t1").via == "db"


def test_inmemory_set_platform_admin_round_trip() -> None:
    registry = InMemoryTenantRegistry()
    registry.create_user(_user(password_hash="digest"))  # pragma: allowlist secret
    assert registry.get_user("t1", "u1").is_platform_admin is False  # type: ignore[union-attr]

    registry.set_platform_admin("t1", "u1", True)
    user = registry.get_user("t1", "u1")
    assert user is not None
    assert user.is_platform_admin is True
    assert user.password_hash is None  # the plain read path still never surfaces the digest

    registry.set_platform_admin("t1", "u1", False)
    assert registry.get_user("t1", "u1").is_platform_admin is False  # type: ignore[union-attr]


def test_inmemory_set_platform_admin_scoped_and_silent_for_unknown_user() -> None:
    registry = InMemoryTenantRegistry()
    registry.create_user(_user())
    registry.set_platform_admin("other-tenant", "u1", True)  # wrong tenant: no-op
    registry.set_platform_admin("t1", "missing", True)  # unknown user: no-op
    assert registry.get_user("t1", "u1").is_platform_admin is False  # type: ignore[union-attr]
