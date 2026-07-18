"""Idempotent dev-tenant seeding (CISO M3): a demo tenant + one user per role for UI login.

This is a DEV convenience, never wired into app startup or migrations (which run unconditionally,
including in prod - the classic default-credentials path, CWE-1392). It is invoked only by
``scripts/seed-dev.sh`` / ``make seed-dev``, which gates on a non-prod environment and a
loopback database before calling :func:`seed_dev`.

The tenant id is ``dev`` (deliberately NOT ``test%``, which the integration-test cleanup wipes).
Passwords are supplied by the caller (env-provided for reproducible logins, else generated + printed
once) - this module never hardcodes a password, so even a mis-run cannot create a well-known
credential.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import Tenant, User

from doktok_core.security.passwords import hash_password

DEV_TENANT_ID = "dev"
DEV_TENANT_NAME = "Dev"

# One account per role so RBAC is actually exercisable from the UI.
DEV_USERS: tuple[tuple[str, str], ...] = (
    ("dev-admin@doktok.local", "admin"),
    ("dev-manager@doktok.local", "admin"),  # tenant admin WITHOUT the platform flag (#620)
    ("dev-editor@doktok.local", "editor"),
    ("dev-viewer@doktok.local", "viewer"),
)

# The dev admin doubles as the platform-owner persona (#613, ADR-0025): it can reach the
# platform-gated surfaces (backup export/restore, DRP) in UI/dev flows. The manager/editor/viewer
# stay non-platform so the denial paths are exercisable with the same seed; the manager in
# particular exercises the restricted tenant-admin view (#620).
DEV_PLATFORM_ADMINS = frozenset({"dev-admin@doktok.local"})

# Length floor for a seeded password (kept in step with the API password policy).
MIN_SEED_PASSWORD_LENGTH = 12

# Seeding is refused outside these environments and off a loopback database (CISO M3 gating).
ALLOWED_SEED_ENVS = frozenset({"local", "dev"})
_LOOPBACK_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def seed_guard(env: str, db_host: str, *, allow_remote: bool) -> str | None:
    """Return an error message if seeding must be refused here, else ``None`` (CISO M3).

    Two independent gates: the environment must be local/dev, and the database must be loopback
    unless ``allow_remote`` is set. This is defense in depth on top of the no-hardcoded-password
    rule - seeded demo credentials must never be creatable against a production system."""
    if env not in ALLOWED_SEED_ENVS:
        return f"refusing to seed: environment is '{env}' (only {sorted(ALLOWED_SEED_ENVS)})"
    if db_host.lower() not in _LOOPBACK_DB_HOSTS and not allow_remote:
        return f"refusing to seed a non-loopback database ({db_host!r}); pass --allow-remote"
    return None


@dataclass
class SeededAccount:
    email: str
    role: str
    created: bool  # True if this run created the user
    password_set: bool  # True if this run set/reset the password
    password: str | None  # the plaintext, ONLY when this run set it (to print once); else None


def seed_dev(
    registry: TenantRegistry,
    *,
    password_for: Callable[[str], str],
    reset: bool = False,
) -> list[SeededAccount]:
    """Create-or-update the ``dev`` tenant + one user per role. Idempotent.

    - Missing user: created active with ``password_for(email)``.
    - Existing user, ``reset=False``: left untouched (password NOT changed) - safe to re-run.
    - Existing user, ``reset=True``: password reset and role re-synced.

    ``password_for`` returns the plaintext to use for a given email; it must satisfy the length
    policy (the caller validates env-provided passwords). Returns what happened, per account, so the
    caller can print the passwords it just set exactly once.
    """
    registry.create_tenant(Tenant(id=DEV_TENANT_ID, name=DEV_TENANT_NAME))  # ON CONFLICT DO NOTHING
    results: list[SeededAccount] = []
    for email, role in DEV_USERS:
        existing = registry.get_user_by_email(DEV_TENANT_ID, email)
        if existing is None:
            password = password_for(email)
            registry.create_user(
                User(
                    id=uuid.uuid4().hex,
                    tenant_id=DEV_TENANT_ID,
                    email=email,
                    display_name=email.split("@")[0],
                    role=role,
                    status="active",
                    is_platform_admin=email in DEV_PLATFORM_ADMINS,
                    password_hash=hash_password(password),
                )
            )
            results.append(
                SeededAccount(email, role, created=True, password_set=True, password=password)
            )
        elif reset:
            password = password_for(email)
            registry.set_user_password(DEV_TENANT_ID, existing.id, hash_password(password))
            registry.set_user_role(DEV_TENANT_ID, existing.id, role)
            registry.set_platform_admin(DEV_TENANT_ID, existing.id, email in DEV_PLATFORM_ADMINS)
            results.append(
                SeededAccount(email, role, created=False, password_set=True, password=password)
            )
        else:
            results.append(
                SeededAccount(email, role, created=False, password_set=False, password=None)
            )
    return results
