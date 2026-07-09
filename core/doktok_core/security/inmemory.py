"""In-memory ``TenantRegistry`` for tests and local runs without a database (#554).

Mirrors :class:`PostgresTenantRegistry` semantics: hashed-token lookup filtered to live
(non-revoked) tokens, plus the minimal provisioning seams. Not thread-safe; for unit tests.
"""

from __future__ import annotations

from doktok_contracts.schemas import ApiToken, Tenant, TokenResolution, User


class InMemoryTenantRegistry:
    def __init__(self) -> None:
        self.tenants: dict[str, Tenant] = {}
        self.users: dict[str, User] = {}
        self.tokens: dict[str, ApiToken] = {}

    def resolve_token(self, token_sha256: str) -> TokenResolution | None:
        for token in self.tokens.values():
            if token.token_sha256 == token_sha256 and token.revoked_at is None:
                return TokenResolution(tenant_id=token.tenant_id, user_id=token.user_id)
        return None

    def create_tenant(self, tenant: Tenant) -> None:
        self.tenants.setdefault(tenant.id, tenant)

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.tenants.get(tenant_id)

    def create_user(self, user: User) -> None:
        self.users.setdefault(user.id, user)

    def get_user(self, tenant_id: str, user_id: str) -> User | None:
        user = self.users.get(user_id)
        return user if user and user.tenant_id == tenant_id else None

    def create_api_token(self, token: ApiToken) -> None:
        self.tokens.setdefault(token.id, token)

    def revoke_api_token(self, tenant_id: str, token_id: str) -> None:
        from datetime import UTC, datetime

        token = self.tokens.get(token_id)
        if token and token.tenant_id == tenant_id and token.revoked_at is None:
            self.tokens[token_id] = token.model_copy(update={"revoked_at": datetime.now(UTC)})
