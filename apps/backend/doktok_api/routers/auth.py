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

from datetime import UTC, datetime
from typing import Annotated

from doktok_contracts.ports import AuditLogRepository, TenantRegistry
from doktok_contracts.schemas import AuditEventType, User
from doktok_core.audit.logger import record_activity
from doktok_core.security.auth import hash_token
from doktok_core.security.passwords import hash_password, validate_password, verify_password
from doktok_core.security.sessions import issue_access_token
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from doktok_api.dependencies import (
    AuthenticatedUser,
    effective_jwt_secret,
    get_audit_repository,
    get_tenant_registry,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

Registry = Annotated[TenantRegistry, Depends(get_tenant_registry)]
Audit = Annotated[AuditLogRepository, Depends(get_audit_repository)]

# A valid scrypt digest of a random value, verified against when the email is unknown so that the
# "no such user" path does the same work as the "wrong password" path (no user-enumeration timing
# oracle). Computed once at import.
_DECOY_HASH = hash_password("decoy-password-never-matches")

_INVALID_CREDENTIALS = "invalid email or password"


class LoginRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    # Plain str (not EmailStr) to avoid pulling in the optional email-validator dependency; the
    # email is only a lookup key here, verified against the stored password. Bounded (F-06): the
    # value feeds a rate-limiter bucket key, so it must not be arbitrarily large.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)


class PublicUser(BaseModel):
    """A user's identity as returned to clients - never includes the credential digest."""

    id: str
    tenant_id: str
    email: str
    display_name: str = ""
    role: str = "viewer"
    is_platform_admin: bool = False  # the SPA reflects platform-only surfaces off this (#613)


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
        is_platform_admin=user.is_platform_admin,
    )


def _require_login_secret(request: Request) -> str:
    secret = effective_jwt_secret(request.app.state.settings)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="user login is not configured (set DOKTOK_AUTH_JWT_SECRET)",
        )
    return secret


def _client_ip(request: Request) -> str:
    """The client IP for the login throttle. Honors ``X-Forwarded-For`` ONLY behind a trusted proxy
    (``DOKTOK_TRUSTED_PROXY``); otherwise a client could spoof the header to dodge the per-IP limit.

    The RIGHTMOST element is used (#621, F-10): it is the hop appended by the trusted proxy itself.
    The shipped Caddy overwrites inbound XFF (single trustworthy element); behind an appending
    proxy (e.g. nginx ``proxy_add_x_forwarded_for``) the left side is client-controlled and must be
    ignored, or the per-IP bucket is trivially dodged by rotating spoofed values.
    """
    if getattr(request.app.state.settings, "trusted_proxy", False):
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _throttle_login(request: Request, tenant_id: str, email: str) -> None:
    """Pre-auth brute-force throttle (CISO M2): per-IP and per-(tenant, email) token buckets, run
    BEFORE any credential work so a throttled attacker gets no timing/enumeration signal. The
    account bucket key is hashed (F-06): a fixed-size key that never embeds the raw email."""
    checks = (
        (getattr(request.app.state, "login_ip_limiter", None), f"ip:{_client_ip(request)}"),
        (
            getattr(request.app.state, "login_acct_limiter", None),
            f"acct:{hash_token(f'{tenant_id}:{email.strip().lower()}')}",
        ),
    )
    for limiter, key in checks:
        if limiter is None:
            continue
        allowed, retry_after = limiter.allow(key)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many login attempts; please wait and try again",
                headers={"Retry-After": str(retry_after)},
            )


def _audit_login(
    audit: AuditLogRepository,
    request: Request,
    tenant_id: str,
    email: str,
    *,
    ok: bool,
    user_id: str | None = None,
) -> None:
    normalized = email.strip().lower()
    record_activity(
        audit,
        tenant_id,
        AuditEventType.AUTH_LOGIN_SUCCEEDED if ok else AuditEventType.AUTH_LOGIN_FAILED,
        actor=user_id or normalized,
        actor_kind="user",
        description=f'Login {"succeeded" if ok else "failed"} for "{normalized}"',
        details={"email": normalized, "ip": _client_ip(request)},  # never the password
    )


class AuthConfig(BaseModel):
    login_enabled: bool


@router.get("/config", response_model=AuthConfig)
def auth_config(request: Request) -> AuthConfig:
    """Whether password login is available (a signing secret is configured). Unauthenticated, so the
    SPA can decide up front to show the login screen vs. run in proxy-injected/token-free mode."""
    return AuthConfig(login_enabled=bool(effective_jwt_secret(request.app.state.settings)))


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest, registry: Registry, audit: Audit) -> LoginResponse:
    """Authenticate an email/password and return a session JWT for the tenant/user."""
    secret = _require_login_secret(request)
    _throttle_login(request, body.tenant_id, body.email)  # 429 before any credential work
    user = registry.get_user_by_email(body.tenant_id, body.email)
    # Always run a verification (decoy when the user is unknown or has no password) so the response
    # time does not reveal whether the account exists. Cap concurrent memory-hard verifications so
    # login cannot exhaust the sync worker pool.
    stored = user.password_hash if user else None
    with request.app.state.login_verify_semaphore:
        password_ok = verify_password(body.password, stored or _DECOY_HASH)
    if user is None or not password_ok or user.status != "active":
        _audit_login(audit, request, body.tenant_id, body.email, ok=False)
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
    _audit_login(audit, request, body.tenant_id, body.email, ok=True, user_id=user.id)
    return LoginResponse(access_token=token, expires_in=ttl, user=_public_user(user))


class AcceptInviteRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=1)
    display_name: str | None = None


@router.post("/accept-invite", response_model=PublicUser)
def accept_invite(body: AcceptInviteRequest, registry: Registry, audit: Audit) -> PublicUser:
    """Accept a tenant invitation (#557): validate the one-time token, set the password, and
    activate the user. Public (no auth) - the invite token is the credential. A single generic
    error avoids disclosing whether a token exists."""
    try:
        validate_password(body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired invitation"
    )
    inv = registry.get_invitation_by_token(hash_token(body.token))
    if inv is None or inv.accepted_at is not None or inv.expires_at <= datetime.now(UTC):
        raise invalid
    user = registry.get_user(inv.tenant_id, inv.user_id)
    if user is None:
        raise invalid
    registry.set_user_password(inv.tenant_id, inv.user_id, hash_password(body.password))
    registry.set_user_status(inv.tenant_id, inv.user_id, "active")
    registry.mark_invitation_accepted(inv.id)
    record_activity(
        audit,
        inv.tenant_id,
        AuditEventType.USER_INVITE_ACCEPTED,
        actor=inv.user_id,
        actor_kind="user",
        record_kind="user",
        record_id=inv.user_id,
        description=f'Invitation accepted by "{user.email}"',
        details={"user_id": inv.user_id},
    )
    activated = registry.get_user(inv.tenant_id, inv.user_id)
    assert activated is not None
    return _public_user(activated)


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
