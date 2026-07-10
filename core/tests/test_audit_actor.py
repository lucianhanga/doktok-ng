from doktok_contracts.schemas import TenantContext
from doktok_core.audit.logger import actor_identity


def test_actor_is_user_id_when_authenticated() -> None:
    ctx = TenantContext(tenant_id="tenant-a", user_id="user-42")
    assert actor_identity(ctx) == "user-42"


def test_actor_falls_back_to_tenant_for_tenant_scoped_token() -> None:
    ctx = TenantContext(tenant_id="tenant-a", user_id=None)
    assert actor_identity(ctx) == "tenant-a"
