from datetime import UTC, datetime, timedelta

from doktok_contracts.schemas import TokenResolution
from doktok_core.security.sessions import decode_access_token, issue_access_token

SECRET = "test-signing-secret"  # pragma: allowlist secret
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_roundtrip_resolves_tenant_and_user() -> None:
    token = issue_access_token(
        tenant_id="tenant-a", user_id="user-1", secret=SECRET, ttl_seconds=3600, now=NOW
    )
    resolution = decode_access_token(token, secret=SECRET, now=NOW + timedelta(minutes=10))
    assert resolution == TokenResolution(tenant_id="tenant-a", user_id="user-1", via="jwt")


def test_expired_token_resolves_none() -> None:
    token = issue_access_token(
        tenant_id="tenant-a", user_id="user-1", secret=SECRET, ttl_seconds=60, now=NOW
    )
    assert decode_access_token(token, secret=SECRET, now=NOW + timedelta(minutes=2)) is None


def test_wrong_secret_resolves_none() -> None:
    token = issue_access_token(
        tenant_id="tenant-a", user_id="user-1", secret=SECRET, ttl_seconds=3600, now=NOW
    )
    wrong_secret = "other-secret"  # pragma: allowlist secret
    assert decode_access_token(token, secret=wrong_secret, now=NOW) is None


def test_tampered_token_resolves_none() -> None:
    token = issue_access_token(
        tenant_id="tenant-a", user_id="user-1", secret=SECRET, ttl_seconds=3600, now=NOW
    )
    assert decode_access_token(token + "x", secret=SECRET, now=NOW) is None


def test_garbage_and_empty_resolve_none() -> None:
    assert decode_access_token("not.a.jwt", secret=SECRET, now=NOW) is None
    assert decode_access_token("", secret=SECRET, now=NOW) is None
    assert decode_access_token("abc", secret="", now=NOW) is None


def test_issue_requires_secret() -> None:
    import pytest

    with pytest.raises(ValueError):
        issue_access_token(tenant_id="t", user_id="u", secret="", ttl_seconds=60, now=NOW)
