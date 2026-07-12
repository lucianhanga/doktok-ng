"""Seed a dev tenant + one user per role for UI login. Invoked by seed-dev.sh / make seed-dev.

Gated to a non-production environment and a loopback database so seeded demo credentials can never
land in production (CWE-1392). Idempotent: re-running does not change existing users unless --reset.
Passwords come from DOKTOK_DEV_SEED_PASSWORD (reproducible logins) or are generated + printed once.
"""

from __future__ import annotations

import os
import secrets
import sys
from urllib.parse import urlparse

from doktok_core.config import get_settings
from doktok_core.dev.seed import (
    DEV_TENANT_ID,
    MIN_SEED_PASSWORD_LENGTH,
    seed_dev,
    seed_guard,
)
from doktok_storage_postgres import Database, PostgresTenantRegistry, migrate

_GREEN = "\033[0;32m"
_RED = "\033[0;31m"
_YELLOW = "\033[1;33m"
_NC = "\033[0m"


def _err(msg: str) -> None:
    print(f"{_RED}{msg}{_NC}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"{_YELLOW}{msg}{_NC}")


def _db_host(dsn: str) -> str:
    try:
        return (urlparse(dsn).hostname or "").lower()
    except ValueError:
        return "?"


def main() -> int:
    reset = "--reset" in sys.argv
    allow_remote = "--allow-remote" in sys.argv
    settings = get_settings()

    # Gates 1+2 (env + loopback DB); Gate 3 (no hardcoded passwords) is enforced below and survives
    # a misconfiguration of the first two.
    refusal = seed_guard(settings.env, _db_host(settings.database_url), allow_remote=allow_remote)
    if refusal is not None:
        _err(refusal)
        return 2

    # Gate 3: passwords are never hardcoded. Env-provided (reproducible) or generated per user.
    env_pw = os.environ.get("DOKTOK_DEV_SEED_PASSWORD", "").strip()
    if env_pw and len(env_pw) < MIN_SEED_PASSWORD_LENGTH:
        _err(f"DOKTOK_DEV_SEED_PASSWORD must be at least {MIN_SEED_PASSWORD_LENGTH} characters")
        return 2

    def password_for(_email: str) -> str:
        return env_pw or secrets.token_urlsafe(16)

    db = Database(settings.database_url)
    try:
        migrate(db)  # ensure the registry tables exist on a fresh dev DB
        accounts = seed_dev(PostgresTenantRegistry(db), password_for=password_for, reset=reset)
    finally:
        db.close()

    print(f"{_GREEN}Seeded tenant '{DEV_TENANT_ID}':{_NC}")
    for a in accounts:
        state = "created" if a.created else ("reset" if a.password_set else "unchanged")
        line = f"  {a.email:<26} {a.role:<7} [{state}]"
        if a.password_set and a.password is not None and not env_pw:
            line += f"  password: {a.password}"
        print(line)
    if env_pw:
        print(f"{_YELLOW}Passwords: DOKTOK_DEV_SEED_PASSWORD (not printed).{_NC}")
    else:
        any_set = any(a.password_set for a in accounts)
        if any_set:
            _warn("Save the generated passwords above - they are shown only once.")
        else:
            print("All users already existed; re-run with --reset to rotate their passwords.")
    print(f"Log in at the UI with tenant '{DEV_TENANT_ID}' and one of the emails above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
