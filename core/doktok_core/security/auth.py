"""Token-to-tenant resolution (ADR-0008).

Resolves a presented bearer token to a tenant id using a constant-time comparison to avoid timing
oracles. The token store is a static ``{token: tenant_id}`` map now; a DB-backed, hashed, revocable
store can replace this behind the same function later.
"""

from __future__ import annotations

import secrets


def resolve_tenant(tokens: dict[str, str], presented: str | None) -> str | None:
    """Return the tenant id for ``presented``, or ``None`` if it matches no configured token.

    Every configured token is compared (constant-time) so the work does not short-circuit on the
    first character of a wrong token.
    """
    if not presented:
        return None
    matched: str | None = None
    for token, tenant_id in tokens.items():
        if secrets.compare_digest(token, presented):
            matched = tenant_id
    return matched
