"""Token-to-tenant resolution (ADR-0008).

Resolves a presented bearer token to a tenant using a constant-time comparison to avoid timing
oracles. Two stores, tried in order (#554):

1. **DB-backed registry** (``TenantRegistry``): the presented token is hashed (sha256) and looked
   up against ``api_tokens``; a match yields tenant + optional user, and supports revocation and
   many tokens per tenant. This is the forward path.
2. **Static env map** (``DOKTOK_TENANT_TOKENS``): the original ``{token: tenant_id}`` map, kept as
   a local-first/dev fallback so single-tenant deployments work with no DB rows. Compared
   constant-time.

The plaintext token is never stored; only its sha256. Hashing a high-entropy random token and
looking it up by an indexed hash is the standard pattern - equality on the digest does not leak the
secret, so a plain indexed lookup is acceptable here (unlike the low-entropy static map, which is
compared constant-time).
"""

from __future__ import annotations

import hashlib
import secrets

from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import TokenResolution


def hash_token(token: str) -> str:
    """The sha256 hex digest used as the ``api_tokens`` lookup key (#554)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_tenant(tokens: dict[str, str], presented: str | None) -> str | None:
    """Return the tenant id for ``presented`` in the static env map, or ``None`` (ADR-0008).

    Every configured token is compared (constant-time) so the work does not short-circuit on the
    first character of a wrong token. Retained for the static-only callers (e.g. the MCP server)
    and used as the fallback tier by :func:`resolve_token`.
    """
    if not presented:
        return None
    matched: str | None = None
    for token, tenant_id in tokens.items():
        if secrets.compare_digest(token, presented):
            matched = tenant_id
    return matched


def resolve_token(
    presented: str | None,
    *,
    registry: TenantRegistry | None = None,
    static_tokens: dict[str, str] | None = None,
) -> TokenResolution | None:
    """Resolve a presented bearer token to a tenant (+ optional user), DB first then static map.

    Returns ``None`` when the token is empty or matches no live DB token and no static entry. The
    DB registry is authoritative when it resolves; the static map is only consulted on a miss so an
    operator can still reach a deployment that has no ``api_tokens`` rows yet.
    """
    if not presented:
        return None
    if registry is not None:
        resolution = registry.resolve_token(hash_token(presented))
        if resolution is not None:
            return resolution
    if static_tokens:
        tenant_id = resolve_tenant(static_tokens, presented)
        if tenant_id is not None:
            # Host-provisioned static token: the deployment's platform tier (#613, ADR-0025).
            return TokenResolution(tenant_id=tenant_id, user_id=None, via="static")
    return None


def _looks_like_jwt(presented: str) -> bool:
    """A JWT is three base64url segments joined by dots. Opaque API tokens never contain two dots,
    so this cheap structural check routes each credential to the right verifier without a wasted DB
    lookup (and without feeding a random token to the signature verifier)."""
    return presented.count(".") == 2


def resolve_credential(
    presented: str | None,
    *,
    registry: TenantRegistry | None = None,
    static_tokens: dict[str, str] | None = None,
    jwt_secret: str | None = None,
) -> TokenResolution | None:
    """Resolve any bearer credential - a login session JWT (#555) or an opaque API token (#554).

    When a ``jwt_secret`` is configured and the credential is JWT-shaped, it is verified as a
    session token first; anything else (or a bad JWT) falls through to :func:`resolve_token` (DB
    registry then the static env map). Returns ``None`` if nothing resolves it.
    """
    if not presented:
        return None
    if jwt_secret and _looks_like_jwt(presented):
        from doktok_core.security.sessions import decode_access_token

        resolution = decode_access_token(presented, secret=jwt_secret)
        if resolution is not None:
            return resolution
    return resolve_token(presented, registry=registry, static_tokens=static_tokens)
