"""RBAC role model (#556, EPIC #523).

Three ordered roles gate write access in the API:

- ``viewer``  - read-only. Any authenticated caller is at least a viewer.
- ``editor``  - viewer + content mutations (ingest, entities, KG edits, chat, ...).
- ``admin``   - editor + administration (settings, backups, tenant/user management).

Enforcement is method-aware and applied per router (see ``make_write_guard`` in the API): safe
methods pass for everyone; unsafe methods require the router's minimum role. User-less DB
api_tokens resolve to the role stored on their row (#645, F-33 - least-privilege machine
credentials); static host-provisioned tokens (``DOKTOK_TENANT_TOKENS``) resolve to ``admin`` -
the local-first single-operator platform tier.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


# Privilege order; a role satisfies a requirement when its rank is >= the required rank.
_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.EDITOR: 1, Role.ADMIN: 2}


def parse_role(value: str | None) -> Role:
    """Coerce a stored/loaded role string to a :class:`Role`, defaulting to the least-privileged
    ``viewer`` for anything unknown or missing (fail-closed - never silently grant more)."""
    if value is None:
        return Role.VIEWER
    try:
        return Role(value)
    except ValueError:
        return Role.VIEWER


def role_at_least(role: Role, minimum: Role) -> bool:
    """True iff ``role`` meets or exceeds ``minimum`` in the privilege order."""
    return _RANK[role] >= _RANK[minimum]
