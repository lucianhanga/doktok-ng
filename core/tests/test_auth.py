import hashlib
from datetime import UTC, datetime

from doktok_contracts.schemas import ApiToken, TokenResolution
from doktok_core.security.auth import (
    hash_token,
    resolve_credential,
    resolve_tenant,
    resolve_token,
)
from doktok_core.security.inmemory import InMemoryTenantRegistry
from doktok_core.security.sessions import issue_access_token

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


def test_resolves_known_token() -> None:
    assert resolve_tenant(TOKENS, "tok-a") == "tenant-a"
    assert resolve_tenant(TOKENS, "tok-b") == "tenant-b"


def test_unknown_or_missing_token_returns_none() -> None:
    assert resolve_tenant(TOKENS, "nope") is None
    assert resolve_tenant(TOKENS, "") is None
    assert resolve_tenant(TOKENS, None) is None
    assert resolve_tenant({}, "tok-a") is None


def test_hash_token_is_sha256_hex() -> None:
    assert hash_token("secret") == hashlib.sha256(b"secret").hexdigest()


def _registry_with(
    token_plain: str, tenant_id: str, user_id: str | None = None
) -> InMemoryTenantRegistry:
    reg = InMemoryTenantRegistry()
    reg.create_api_token(
        ApiToken(
            id="t1",
            tenant_id=tenant_id,
            user_id=user_id,
            token_sha256=hash_token(token_plain),
            token_prefix=token_plain[:4],
        )
    )
    return reg


def test_resolve_token_prefers_db_registry() -> None:
    reg = _registry_with("db-token", "tenant-db", user_id="user-1")
    assert resolve_token("db-token", registry=reg, static_tokens=TOKENS) == TokenResolution(
        tenant_id="tenant-db", user_id="user-1"
    )


def test_resolve_token_falls_back_to_static_map() -> None:
    reg = InMemoryTenantRegistry()  # empty DB
    assert resolve_token("tok-a", registry=reg, static_tokens=TOKENS) == TokenResolution(
        tenant_id="tenant-a", user_id=None, via="static"
    )


def test_resolve_token_none_when_no_match() -> None:
    assert resolve_token("nope", registry=InMemoryTenantRegistry(), static_tokens=TOKENS) is None
    assert resolve_token("", registry=InMemoryTenantRegistry(), static_tokens=TOKENS) is None
    assert resolve_token(None, static_tokens=TOKENS) is None


def test_resolve_token_revoked_db_token_does_not_resolve() -> None:
    reg = _registry_with("db-token", "tenant-db")
    reg.revoke_api_token("tenant-db", "t1")
    # Revoked DB token must not resolve, and must NOT silently fall through to a same-value static
    # entry (there is none here), so the result is None.
    assert resolve_token("db-token", registry=reg, static_tokens=TOKENS) is None


def test_resolve_token_works_without_registry() -> None:
    assert resolve_token("tok-b", static_tokens=TOKENS) == TokenResolution(
        tenant_id="tenant-b", user_id=None, via="static"
    )


JWT_SECRET = "credential-secret"  # pragma: allowlist secret


def test_resolve_credential_accepts_session_jwt() -> None:
    token = issue_access_token(
        tenant_id="tenant-x",
        user_id="user-9",
        secret=JWT_SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    assert resolve_credential(
        token, jwt_secret=JWT_SECRET, static_tokens=TOKENS
    ) == TokenResolution(tenant_id="tenant-x", user_id="user-9", via="jwt")


def test_resolve_credential_falls_through_to_opaque_token() -> None:
    # A non-JWT opaque token still resolves via the static map even when a jwt_secret is configured.
    assert resolve_credential(
        "tok-a", jwt_secret=JWT_SECRET, static_tokens=TOKENS
    ) == TokenResolution(tenant_id="tenant-a", user_id=None, via="static")


def test_resolve_credential_bad_jwt_does_not_resolve() -> None:
    # JWT-shaped but signed by a different secret -> not resolved, nor matched as an opaque token.
    forged = issue_access_token(
        tenant_id="tenant-x", user_id="u", secret="wrong", ttl_seconds=3600, now=datetime.now(UTC)
    )
    assert resolve_credential(forged, jwt_secret=JWT_SECRET, static_tokens=TOKENS) is None


def test_resolve_credential_ignores_jwt_when_no_secret() -> None:
    token = issue_access_token(
        tenant_id="tenant-x",
        user_id="u",
        secret=JWT_SECRET,
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    # No jwt_secret configured: the JWT is treated as an opaque token and matches nothing.
    assert resolve_credential(token, static_tokens=TOKENS) is None
