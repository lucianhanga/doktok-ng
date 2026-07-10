"""Tenant / member administration API (#559, EPIC #523).

Admin-only endpoints (gated by ``require_admin`` at the router include) to provision the tenants,
users, roles, and API tokens that the auth (#555) and RBAC (#556) layers assume exist. Every
mutating action is audited with the acting admin as the actor (#560).

Security notes:
- Credential material is never returned on a read: user listings exclude the password hash, token
  listings expose only a short ``token_prefix``. An issued API token's plaintext is returned EXACTLY
  ONCE, at creation, and is never stored (only its sha256).
- Users, tokens, and role changes are scoped to the caller's tenant; a caller cannot read or mutate
  another tenant's members.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import ApiToken, AuditEventType, Invitation, Tenant, User
from doktok_core.audit.logger import actor_identity, record_activity
from doktok_core.security.auth import hash_token
from doktok_core.security.passwords import hash_password, validate_password
from doktok_core.security.roles import Role
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from doktok_api.dependencies import AdminUser, get_audit_repository, get_tenant_registry

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

Registry = Annotated[TenantRegistry, Depends(get_tenant_registry)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]

_TOKEN_BYTES = 32  # 256 bits of entropy for an issued API token
_PREFIX_LEN = 8


def _valid_role(value: str) -> str:
    try:
        return Role(value).value
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be one of: viewer, editor, admin",
        ) from None


def _valid_password(value: str) -> str:
    try:
        validate_password(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return value


# --- views / requests ---


class AdminUserView(BaseModel):
    """A member as returned to admins - never includes the credential digest."""

    id: str
    email: str
    display_name: str = ""
    role: str
    status: str


def _user_view(user: User) -> AdminUserView:
    return AdminUserView(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
    )


class CreateTenantRequest(BaseModel):
    # The tenant id is server-generated (an opaque, immutable UUID) - clients supply only a
    # human-readable display name. This avoids id collisions/squatting/enumeration and keeps the id
    # stable and independent of the (renameable) name (multi-tenant best practice).
    name: str = Field(min_length=1)


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3)
    display_name: str = ""
    role: str = "viewer"
    password: str | None = None


class SetRoleRequest(BaseModel):
    role: str


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class TokenView(BaseModel):
    """API-token metadata for listings - never the secret (only its short prefix)."""

    id: str
    user_id: str | None = None
    name: str = ""
    token_prefix: str = ""
    active: bool


class CreateTokenRequest(BaseModel):
    name: str = ""
    user_id: str | None = None


class IssuedToken(BaseModel):
    """The one-time response to token creation. ``token`` is shown ONCE and never persisted."""

    id: str
    token: str
    token_prefix: str
    user_id: str | None = None
    name: str = ""


# --- context ---


class AdminContext(BaseModel):
    """The admin caller's own tenant + identity, for the admin console header (#559).

    Resolves what ``/auth/me`` cannot: the caller may present a tenant-scoped token (no user), which
    still administers its tenant. ``role`` is the user's role, or ``admin`` for a tenant-scoped
    token (it already passed ``require_admin``). ``tenant_name`` falls back to the id for a
    config-defined static tenant that has no ``tenants`` row.
    """

    tenant_id: str
    tenant_name: str
    user_id: str | None = None
    email: str | None = None
    role: str


@router.get("/context", response_model=AdminContext)
def admin_context(caller: AdminUser, registry: Registry) -> AdminContext:
    tenant = registry.get_tenant(caller.tenant_id)
    user = registry.get_user(caller.tenant_id, caller.user_id) if caller.user_id else None
    return AdminContext(
        tenant_id=caller.tenant_id,
        tenant_name=tenant.name if tenant else caller.tenant_id,
        user_id=caller.user_id,
        email=user.email if user else None,
        role=user.role if user else "admin",
    )


# --- tenants ---


@router.get("/tenants", response_model=list[Tenant])
def list_tenants(caller: AdminUser, registry: Registry) -> list[Tenant]:
    return registry.list_tenants()


@router.post("/tenants", response_model=Tenant, status_code=status.HTTP_201_CREATED)
def create_tenant(
    body: CreateTenantRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> Tenant:
    tenant_id = str(uuid.uuid4())  # server-generated opaque GUID
    registry.create_tenant(Tenant(id=tenant_id, name=body.name))
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.TENANT_CREATED,
        actor=actor_identity(caller),
        actor_kind="user",
        description=f'Tenant "{body.name}" ({tenant_id}) created',
        details={"tenant": tenant_id},
    )
    created = registry.get_tenant(tenant_id)
    assert created is not None
    return created


# --- users / members ---


@router.get("/users", response_model=list[AdminUserView])
def list_users(caller: AdminUser, registry: Registry) -> list[AdminUserView]:
    return [_user_view(u) for u in registry.list_users(caller.tenant_id)]


@router.post("/users", response_model=AdminUserView, status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> AdminUserView:
    role = _valid_role(body.role)
    if registry.get_user_by_email(caller.tenant_id, body.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="a user with this email already exists"
        )
    user = User(
        id=uuid.uuid4().hex,
        tenant_id=caller.tenant_id,
        email=body.email.strip(),
        display_name=body.display_name,
        role=role,
        password_hash=hash_password(_valid_password(body.password)) if body.password else None,
    )
    registry.create_user(user)
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.USER_CREATED,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="user",
        record_id=user.id,
        description=f'User "{user.email}" created with role {role}',
        details={"user_id": user.id, "role": role},
    )
    return _user_view(user)


@router.post("/users/{user_id}/role", response_model=AdminUserView)
def set_user_role(
    user_id: str, body: SetRoleRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> AdminUserView:
    role = _valid_role(body.role)
    user = registry.get_user(caller.tenant_id, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
    registry.set_user_role(caller.tenant_id, user_id, role)
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.USER_ROLE_CHANGED,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="user",
        record_id=user_id,
        description=f'Role for "{user.email}" changed from {user.role} to {role}',
        details={"user_id": user_id, "from": user.role, "to": role},
    )
    updated = registry.get_user(caller.tenant_id, user_id)
    assert updated is not None
    return _user_view(updated)


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def set_user_password(
    user_id: str, body: SetPasswordRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> None:
    user = registry.get_user(caller.tenant_id, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
    registry.set_user_password(
        caller.tenant_id, user_id, hash_password(_valid_password(body.password))
    )
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.USER_PASSWORD_RESET,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="user",
        record_id=user_id,
        description=f'Password reset for "{user.email}"',
        details={"user_id": user_id},
    )


def _set_status(
    user_id: str,
    new_status: str,
    event: AuditEventType,
    verb: str,
    caller: AdminUser,
    registry: Registry,
    audit: Audit,
) -> AdminUserView:
    user = registry.get_user(caller.tenant_id, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
    if user_id == caller.user_id and new_status != "active":
        # Guard against an admin locking themselves out mid-session.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="you cannot deactivate yourself"
        )
    registry.set_user_status(caller.tenant_id, user_id, new_status)
    record_activity(
        audit,
        caller.tenant_id,
        event,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="user",
        record_id=user_id,
        description=f'User "{user.email}" {verb}',
        details={"user_id": user_id, "status": new_status},
    )
    updated = registry.get_user(caller.tenant_id, user_id)
    assert updated is not None
    return _user_view(updated)


@router.post("/users/{user_id}/deactivate", response_model=AdminUserView)
def deactivate_user(
    user_id: str, caller: AdminUser, registry: Registry, audit: Audit
) -> AdminUserView:
    """Deactivate a user - immediately blocks all their sessions/tokens (#557)."""
    return _set_status(
        user_id,
        "deactivated",
        AuditEventType.USER_DEACTIVATED,
        "deactivated",
        caller,
        registry,
        audit,
    )


@router.post("/users/{user_id}/reactivate", response_model=AdminUserView)
def reactivate_user(
    user_id: str, caller: AdminUser, registry: Registry, audit: Audit
) -> AdminUserView:
    """Reactivate a previously deactivated user (#557)."""
    return _set_status(
        user_id, "active", AuditEventType.USER_REACTIVATED, "reactivated", caller, registry, audit
    )


# --- invitations (#557) ---


class InviteRequest(BaseModel):
    email: str = Field(min_length=3)
    display_name: str = ""
    role: str = "viewer"


class IssuedInvitation(BaseModel):
    """One-time invitation response. ``token`` is shown ONCE - the admin shares the accept link."""

    user_id: str
    email: str
    role: str
    token: str
    expires_at: datetime


@router.post("/invitations", response_model=IssuedInvitation, status_code=status.HTTP_201_CREATED)
def invite_user(
    request: Request, body: InviteRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> IssuedInvitation:
    """Invite an email to the tenant: creates an ``invited``-status user and a one-time acceptance
    token (returned once). The invitee accepts via POST /auth/accept-invite to set a password."""
    role = _valid_role(body.role)
    if registry.get_user_by_email(caller.tenant_id, body.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="a user with this email already exists"
        )
    user = User(
        id=uuid.uuid4().hex,
        tenant_id=caller.tenant_id,
        email=body.email.strip(),
        display_name=body.display_name,
        role=role,
        status="invited",
    )
    registry.create_user(user)
    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    ttl_hours = request.app.state.settings.auth_invite_ttl_hours
    expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
    registry.create_invitation(
        Invitation(
            id=uuid.uuid4().hex,
            tenant_id=caller.tenant_id,
            user_id=user.id,
            email=user.email,
            role=role,
            token_sha256=hash_token(plaintext),
            expires_at=expires_at,
        )
    )
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.USER_INVITED,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="user",
        record_id=user.id,
        description=f'Invited "{user.email}" as {role}',
        details={"user_id": user.id, "role": role},
    )
    return IssuedInvitation(
        user_id=user.id, email=user.email, role=role, token=plaintext, expires_at=expires_at
    )


# --- API tokens ---


@router.get("/tokens", response_model=list[TokenView])
def list_tokens(caller: AdminUser, registry: Registry) -> list[TokenView]:
    return [
        TokenView(
            id=t.id,
            user_id=t.user_id,
            name=t.name,
            token_prefix=t.token_prefix,
            active=t.revoked_at is None,
        )
        for t in registry.list_api_tokens(caller.tenant_id)
    ]


@router.post("/tokens", response_model=IssuedToken, status_code=status.HTTP_201_CREATED)
def create_token(
    body: CreateTokenRequest, caller: AdminUser, registry: Registry, audit: Audit
) -> IssuedToken:
    if body.user_id is not None and registry.get_user(caller.tenant_id, body.user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such user")
    plaintext = secrets.token_urlsafe(_TOKEN_BYTES)
    token_id = uuid.uuid4().hex
    registry.create_api_token(
        ApiToken(
            id=token_id,
            tenant_id=caller.tenant_id,
            user_id=body.user_id,
            token_sha256=hash_token(plaintext),
            token_prefix=plaintext[:_PREFIX_LEN],
            name=body.name,
        )
    )
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.API_TOKEN_ISSUED,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="api_token",
        record_id=token_id,
        description=f'API token "{body.name or token_id[:8]}" issued',
        details={"token_id": token_id, "user_id": body.user_id},
    )
    return IssuedToken(
        id=token_id,
        token=plaintext,
        token_prefix=plaintext[:_PREFIX_LEN],
        user_id=body.user_id,
        name=body.name,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: str, caller: AdminUser, registry: Registry, audit: Audit) -> None:
    registry.revoke_api_token(caller.tenant_id, token_id)
    record_activity(
        audit,
        caller.tenant_id,
        AuditEventType.API_TOKEN_REVOKED,
        actor=actor_identity(caller),
        actor_kind="user",
        record_kind="api_token",
        record_id=token_id,
        description="API token revoked",
        details={"token_id": token_id},
    )
