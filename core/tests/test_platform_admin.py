"""The platform tier as a HOST credential (#701, epic #700): the credential-resolution tiers.

There is no user-level platform admin anymore - the flag, its grant endpoint, and the registry
setter are gone. The tier survives only as ``via == "static"`` on the resolution: a
host-provisioned static token (the console credential). The ``via`` marker on
``TokenResolution`` still records WHICH tier resolved a credential so the API can tell host
credentials from user ones.
"""

from __future__ import annotations

from doktok_contracts.schemas import ApiToken, TenantContext, TokenResolution
from doktok_core.security.auth import hash_token, resolve_token
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import decode_access_token, issue_access_token

_SECRET = "s" * 32  # test-only signing secret


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
