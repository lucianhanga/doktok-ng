"""One idempotent action that makes a tenant actually usable (the shared provisioning core).

A tenant is only usable when three planes agree: the DB registry row exists (identity + status),
its filesystem lifecycle folders exist (storage), and there is a credential to reach it. Creating a
tenant row alone (the old admin-API behavior) produced a dead tenant. This function does all three,
idempotently, so BOTH the admin API and ``make create-tenant`` provision the same way.

The tenant id is a server-generated opaque UUID by default. A caller-supplied id is validated
against a strict allowlist before it is ever used as a filesystem path segment (``FilesystemLayout``
does no validation of its own), closing a path-traversal hole.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass

from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import ApiToken, Tenant

from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.security.auth import hash_token

# A tenant id becomes a filesystem path segment, so it must be path-safe: no separators, no ``..``.
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BOOTSTRAP_TOKEN_BYTES = 32  # 256 bits, mirrors the admin token issuer
_PREFIX_LEN = 8
# Both intake folders the worker watches per tenant (standard + enhanced re-OCR).
_INGEST_DIRS = ("ingest", "ingest.enhanced")


class InvalidTenantId(ValueError):
    """Raised when a caller-supplied tenant id is not path-safe."""


def validate_tenant_id(tenant_id: str) -> str:
    if not _TENANT_ID_RE.match(tenant_id):
        raise InvalidTenantId(
            f"invalid tenant id {tenant_id!r}: must match {_TENANT_ID_RE.pattern} "
            "(letters, digits, '-', '_'; no path separators)"
        )
    return tenant_id


@dataclass
class ProvisionedTenant:
    tenant_id: str
    name: str
    created: bool  # True iff this call created the registry row (False = it already existed)
    token: str | None  # the one-time bootstrap admin token, if one was issued (shown once)
    folders_root: str  # the tenant's filesystem base directory


def provision_tenant(
    registry: TenantRegistry,
    files_root: str,
    *,
    name: str,
    tenant_id: str | None = None,
    issue_token: bool = True,
) -> ProvisionedTenant:
    """Create-or-ensure a usable tenant: registry row (active) + lifecycle folders + an optional
    one-time bootstrap admin token. Idempotent on the row and folders; a fresh token is minted each
    call it is asked to (tokens are additive - you cannot recover a lost one).

    The bootstrap token is tenant-scoped (no user), so it resolves to admin of the new tenant - the
    operator points ``DOKTOK_DEV_TOKEN`` / the UI at it and can administer immediately, with no env
    edit (auth is DB-first). Returns what happened so the caller can reveal the token exactly once.
    """
    tid = validate_tenant_id(tenant_id) if tenant_id is not None else str(uuid.uuid4())
    existing = registry.get_tenant(tid)
    registry.create_tenant(Tenant(id=tid, name=name))  # ON CONFLICT DO NOTHING (idempotent)

    base = FilesystemLayout(files_root, tid).base
    for ingest_dir in _INGEST_DIRS:
        FilesystemLayout(files_root, tid, ingest_dir=ingest_dir).ensure()

    token: str | None = None
    if issue_token:
        plaintext = secrets.token_urlsafe(_BOOTSTRAP_TOKEN_BYTES)
        registry.create_api_token(
            ApiToken(
                id=uuid.uuid4().hex,
                tenant_id=tid,
                user_id=None,  # tenant-scoped => admin
                token_sha256=hash_token(plaintext),
                token_prefix=plaintext[:_PREFIX_LEN],
                name="bootstrap",
            )
        )
        token = plaintext

    return ProvisionedTenant(
        tenant_id=tid, name=name, created=existing is None, token=token, folders_root=str(base)
    )
