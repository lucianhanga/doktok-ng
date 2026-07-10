"""User authentication: password login -> session JWT (#555, EPIC #523).

``POST /auth/login`` verifies an email/password against the tenant/user registry (#554) and, on
success, mints a short-lived HS256 session token (:mod:`doktok_core.security.sessions`). The token
is then presented as a normal ``Authorization: Bearer <jwt>`` header and resolves through the same
seam as opaque API tokens, so every existing endpoint accepts it with no per-route change.

Local-first posture: login is OPT-IN. With no signing secret configured (neither
``DOKTOK_AUTH_JWT_SECRET`` nor ``DOKTOK_SECRETS_KEY``) the endpoint reports 503 and the static
token paths keep working unchanged. Failures are reported with a single generic message so the API
does not disclose whether an email exists.
"""

from __future__ import annotations

from typing import Annotated

from doktok_contracts.ports import TenantRegistry
from doktok_contracts.schemas import User
from doktok_core.security.passwords import hash_password, verify_password
from doktok_core.security.sessions import issue_access_token
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from doktok_api.dependencies import (
    AuthenticatedUser,
    effective_jwt_secret,
    get_tenant_registry,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

Registry = Annotated[TenantRegistry, Depends(get_tenant_registry)]

# A valid scrypt digest of a random value, verified against when the email is unknown so that the
# "no such user" path does the same work as the "wrong password" path (no user-enumeration timing
# oracle). Computed once at import.
_DECOY_HASH = hash_password("decoy-password-never-matches")

_INVALID_CREDENTIALS = "invalid email or password"


class LoginRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    # Plain str (not EmailStr) to avoid pulling in the optional email-validator dependency; the
    # email is only a lookup key here, verified against the stored password.
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class PublicUser(BaseModel):
    """A user's identity as returned to clients - never includes the credential digest."""

    id: str
    tenant_id: str
    email: str
    display_name: str = ""
    role: str = "viewer"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: PublicUser


def _public_user(user: User) -> PublicUser:
    """Project a registry ``User`` to its client-safe shape (drops the credential digest)."""
    return PublicUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


def _require_login_secret(request: Request) -> str:
    secret = effective_jwt_secret(request.app.state.settings)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user login is not configured (set DOKTOK_AUTH_JWT_SECRET)",
        )
    return secret


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest, registry: Registry) -> LoginResponse:
    """Authenticate an email/password and return a session JWT for the tenant/user."""
    secret = _require_login_secret(request)
    user = registry.get_user_by_email(body.tenant_id, body.email)
    # Always run a verification (decoy when the user is unknown or has no password) so the response
    # time does not reveal whether the account exists.
    stored = user.password_hash if user else None
    password_ok = verify_password(body.password, stored or _DECOY_HASH)
    if user is None or not password_ok or user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings = request.app.state.settings
    ttl = settings.auth_access_ttl_seconds
    token = issue_access_token(
        tenant_id=user.tenant_id, user_id=user.id, secret=secret, ttl_seconds=ttl
    )
    return LoginResponse(access_token=token, expires_in=ttl, user=_public_user(user))


@router.get("/me", response_model=PublicUser)
def me(request: Request, caller: AuthenticatedUser, registry: Registry) -> PublicUser:
    """The caller's identity. Requires a user-scoped credential (session JWT or user API token)."""
    assert caller.user_id is not None  # guaranteed by require_user
    user = registry.get_user(caller.tenant_id, caller.user_id)
    if user is None:
        # A validly-signed token for a user that no longer exists (deleted between calls).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _public_user(user)
