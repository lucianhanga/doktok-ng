"""Stateless session tokens (JWT) for user login (#555, EPIC #523).

A successful login mints a short-lived HS256 JWT that carries the tenant and user identity. It is
presented as a normal ``Authorization: Bearer <jwt>`` header, so it flows through the same
resolution seam (:func:`doktok_core.security.auth.resolve_credential`) as opaque API tokens - no
server-side session store is needed (local-first: nothing extra to run or persist).

The signing secret is an operator secret (``DOKTOK_AUTH_JWT_SECRET``, falling back to
``DOKTOK_SECRETS_KEY``). Rotating it invalidates outstanding sessions, which is the intended
revoke-all lever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from doktok_contracts.schemas import TokenResolution

_ALG = "HS256"
_TYP = "access"


def issue_access_token(
    *,
    tenant_id: str,
    user_id: str,
    secret: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    """Mint a signed access token for ``user_id`` in ``tenant_id``, expiring in ``ttl_seconds``."""
    if not secret:
        raise ValueError("a signing secret is required to issue access tokens")
    issued = now or datetime.now(UTC)
    payload = {
        "typ": _TYP,
        "sub": user_id,
        "tenant": tenant_id,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=_ALG)


def decode_access_token(
    token: str, *, secret: str, now: datetime | None = None
) -> TokenResolution | None:
    """Validate ``token`` and return its tenant/user, or ``None`` if invalid/expired/wrong type.

    The signature is verified by PyJWT; expiry is checked against ``now`` (injectable for tests) so
    the check is deterministic. Any malformed/tampered/expired token resolves to ``None`` - callers
    treat that as an authentication failure.
    """
    if not secret or not token:
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[_ALG],
            options={"verify_exp": False, "require": ["exp", "sub", "tenant"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != _TYP:
        return None
    current = now or datetime.now(UTC)
    try:
        if int(payload["exp"]) <= int(current.timestamp()):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    return TokenResolution(tenant_id=str(payload["tenant"]), user_id=str(payload["sub"]), via="jwt")
