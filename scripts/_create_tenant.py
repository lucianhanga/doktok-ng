"""Provision a usable tenant from the CLI. Invoked by create-tenant.sh / make create-tenant.

Creates the DB registry row, its filesystem lifecycle folders, and a one-time bootstrap admin token
(all via the shared ``provision_tenant`` core the admin API also uses). Optionally creates a first
admin user you can log in with. The worker picks the tenant up on its next start (env-map edit no
longer required). Unlike the dev seed, this creates a REAL tenant, so it is allowed in any
environment - but it refuses a non-loopback database without --allow-remote and asks to confirm
outside local/dev.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from urllib.parse import urlparse

from doktok_contracts.schemas import User
from doktok_core.config import get_settings
from doktok_core.security.passwords import hash_password, validate_password
from doktok_core.tenants.provisioning import InvalidTenantId, provision_tenant
from doktok_storage_postgres import Database, PostgresTenantRegistry, migrate

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})
_GREEN = "\033[0;32m"
_RED = "\033[0;31m"
_YELLOW = "\033[1;33m"
_NC = "\033[0m"


def _err(msg: str) -> None:
    print(f"{_RED}{msg}{_NC}", file=sys.stderr)


def _db_host(dsn: str) -> str:
    try:
        return (urlparse(dsn).hostname or "").lower()
    except ValueError:
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser(prog="create-tenant")
    ap.add_argument("name", help="human display name for the tenant")
    ap.add_argument("--id", help="explicit tenant id (default: a generated UUID)")
    ap.add_argument("--admin-email", help="also create a first admin user with this email")
    ap.add_argument("--admin-password", help="password for --admin-email (>= 12 chars)")
    ap.add_argument(
        "--platform-admin",
        action="store_true",
        help="make the --admin-email user a platform admin (#613, ADR-0025): they can reach the "
        "deployment-spanning surfaces (backup export/restore, DRP, tenant provisioning)",
    )
    ap.add_argument("--no-token", action="store_true", help="do not mint a bootstrap admin token")
    ap.add_argument("--allow-remote", action="store_true", help="permit a non-loopback database")
    ap.add_argument("-y", "--yes", action="store_true", help="skip the outside-local/dev confirm")
    args = ap.parse_args()

    settings = get_settings()

    host = _db_host(settings.database_url)
    if host not in _LOOPBACK_HOSTS and not args.allow_remote:
        _err(f"refusing a non-loopback database ({host!r}); pass --allow-remote to override")
        return 2
    if settings.env not in ("local", "dev") and not args.yes:
        print(f"{_YELLOW}Environment is '{settings.env}'. Type the tenant name to confirm:{_NC}")
        if input().strip() != args.name:
            _err("confirmation did not match; aborting.")
            return 1

    if args.admin_email:
        if not args.admin_password:
            _err("--admin-email requires --admin-password")
            return 2
        try:
            validate_password(args.admin_password)
        except ValueError as exc:
            _err(str(exc))
            return 2

    db = Database(settings.database_url)
    try:
        migrate(db)
        registry = PostgresTenantRegistry(db)
        try:
            result = provision_tenant(
                registry,
                settings.files_root,
                name=args.name,
                tenant_id=args.id,
                issue_token=not args.no_token,
            )
        except InvalidTenantId as exc:
            _err(str(exc))
            return 2

        admin_line = ""
        if args.admin_email:
            registry.create_user(
                User(
                    id=uuid.uuid4().hex,
                    tenant_id=result.tenant_id,
                    email=args.admin_email.strip(),
                    role="admin",
                    status="active",
                    is_platform_admin=args.platform_admin,
                    password_hash=hash_password(args.admin_password),
                )
            )
            platform_note = ", platform admin" if args.platform_admin else ""
            admin_line = (
                f"  admin user:    {args.admin_email} (role admin{platform_note})"
                " - log in with this tenant id"
            )
    finally:
        db.close()

    verb = "created" if result.created else "already existed (folders/row ensured)"
    print(f"{_GREEN}Tenant '{result.name}' {verb}.{_NC}")
    print(f"  id:            {result.tenant_id}")
    print(f"  folders:       {result.folders_root}/{{ingest, ingest.enhanced, ...}}  [ensured]")
    if result.token:
        print(f"{_YELLOW}  BOOTSTRAP ADMIN TOKEN (shown once - store it now):{_NC}")
        print(f"    {result.token}")
        print("  Point a client at this tenant (no env-map edit needed - auth is DB-first):")
        print(f"    export DOKTOK_DEV_TOKEN={result.token}")
    if admin_line:
        print(admin_line)
    print(f"{_YELLOW}  Restart the worker to begin watching this tenant (make run-worker).{_NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
