"""In-memory ``TenantRegistry`` for tests and local runs without a database (#554).

Mirrors :class:`PostgresTenantRegistry` semantics: hashed-token lookup filtered to live
(non-revoked) tokens, plus the minimal provisioning seams. Not thread-safe; for unit tests.
"""

from __future__ import annotations

from doktok_contracts.schemas import ApiToken, Invitation, Tenant, TokenResolution, User


class InMemoryTenantRegistry:
    def __init__(self) -> None:
        self.tenants: dict[str, Tenant] = {}
        self.users: dict[str, User] = {}
        self.tokens: dict[str, ApiToken] = {}
        self.invitations: dict[str, Invitation] = {}

    def resolve_token(self, token_sha256: str) -> TokenResolution | None:
        for token in self.tokens.values():
            if token.token_sha256 == token_sha256 and token.revoked_at is None:
                return TokenResolution(
                    tenant_id=token.tenant_id, user_id=token.user_id, role=token.role
                )
        return None

    def create_tenant(self, tenant: Tenant) -> None:
        self.tenants.setdefault(tenant.id, tenant)

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.tenants.get(tenant_id)

    def list_tenants(self) -> list[Tenant]:
        return sorted(self.tenants.values(), key=lambda t: t.id)

    def create_user(self, user: User) -> None:
        self.users.setdefault(user.id, user)

    def get_user(self, tenant_id: str, user_id: str) -> User | None:
        user = self.users.get(user_id)
        if not user or user.tenant_id != tenant_id:
            return None
        # Mirror the DB read path: the plain read does NOT surface the credential digest.
        return user.model_copy(update={"password_hash": None})

    def get_user_by_email(self, tenant_id: str, email: str) -> User | None:
        needle = email.strip().lower()
        for user in self.users.values():
            if user.tenant_id == tenant_id and user.email.lower() == needle:
                return user
        return None

    def list_users(self, tenant_id: str) -> list[User]:
        users = [u for u in self.users.values() if u.tenant_id == tenant_id]
        # Mirror the DB read path: listing never surfaces the credential digest.
        return sorted(
            (u.model_copy(update={"password_hash": None}) for u in users),
            key=lambda u: u.email.lower(),
        )

    def set_user_password(self, tenant_id: str, user_id: str, password_hash: str) -> None:
        user = self.users.get(user_id)
        if user and user.tenant_id == tenant_id:
            self.users[user_id] = user.model_copy(update={"password_hash": password_hash})

    def set_user_role(self, tenant_id: str, user_id: str, role: str) -> None:
        user = self.users.get(user_id)
        if user and user.tenant_id == tenant_id:
            self.users[user_id] = user.model_copy(update={"role": role})

    def set_user_status(self, tenant_id: str, user_id: str, status: str) -> None:
        user = self.users.get(user_id)
        if user and user.tenant_id == tenant_id:
            self.users[user_id] = user.model_copy(update={"status": status})

    def set_platform_admin(self, tenant_id: str, user_id: str, value: bool) -> None:
        user = self.users.get(user_id)
        if user and user.tenant_id == tenant_id:
            self.users[user_id] = user.model_copy(update={"is_platform_admin": value})

    def create_invitation(self, invitation: Invitation) -> None:
        self.invitations.setdefault(invitation.id, invitation)

    def get_invitation_by_token(self, token_sha256: str) -> Invitation | None:
        for inv in self.invitations.values():
            if inv.token_sha256 == token_sha256:
                return inv
        return None

    def mark_invitation_accepted(self, invitation_id: str) -> None:
        from datetime import UTC, datetime

        inv = self.invitations.get(invitation_id)
        if inv and inv.accepted_at is None:
            self.invitations[invitation_id] = inv.model_copy(
                update={"accepted_at": datetime.now(UTC)}
            )

    def accept_invitation(
        self, tenant_id: str, user_id: str, invitation_id: str, password_hash: str
    ) -> bool:
        # F-36 (#648): claim + password-set + activation as one unit; an already-claimed
        # invitation loses.
        inv = self.invitations.get(invitation_id)
        if inv is None or inv.accepted_at is not None:
            return False
        self.mark_invitation_accepted(invitation_id)
        self.set_user_password(tenant_id, user_id, password_hash)
        self.set_user_status(tenant_id, user_id, "active")
        return True

    def create_api_token(self, token: ApiToken) -> None:
        self.tokens.setdefault(token.id, token)

    def list_api_tokens(self, tenant_id: str) -> list[ApiToken]:
        toks = [t for t in self.tokens.values() if t.tenant_id == tenant_id]
        return sorted(toks, key=lambda t: (t.created_at is None, t.created_at), reverse=True)

    def revoke_api_token(self, tenant_id: str, token_id: str) -> None:
        from datetime import UTC, datetime

        token = self.tokens.get(token_id)
        if token and token.tenant_id == tenant_id and token.revoked_at is None:
            self.tokens[token_id] = token.model_copy(update={"revoked_at": datetime.now(UTC)})
