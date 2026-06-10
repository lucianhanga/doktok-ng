from doktok_core.security.auth import resolve_tenant

TOKENS = {"tok-a": "tenant-a", "tok-b": "tenant-b"}


def test_resolves_known_token() -> None:
    assert resolve_tenant(TOKENS, "tok-a") == "tenant-a"
    assert resolve_tenant(TOKENS, "tok-b") == "tenant-b"


def test_unknown_or_missing_token_returns_none() -> None:
    assert resolve_tenant(TOKENS, "nope") is None
    assert resolve_tenant(TOKENS, "") is None
    assert resolve_tenant(TOKENS, None) is None
    assert resolve_tenant({}, "tok-a") is None
