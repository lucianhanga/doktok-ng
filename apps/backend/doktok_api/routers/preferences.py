"""Per-user server-side UI preferences (#558, EPIC #523).

A small key/value store so UI preferences (documents-list layout, thumbnail size, chat
mode/reasoning, insights sub-tab, ...) sync across devices instead of living in per-browser
localStorage. Scoped to the caller's identity via ``actor_identity``: a logged-in user gets their
own bucket; a tenant-scoped (login-less) static-token caller gets one persistent per-tenant bucket,
so the local-first single-operator deployment works with no login.

Any authenticated caller may read and write THEIR OWN preferences - including a viewer (this is not
a privileged action), so the router carries no role write-guard. The store is opaque: the UI owns
the key vocabulary and value shapes; the backend just persists JSON.
"""

from __future__ import annotations

from typing import Annotated, Any

from doktok_contracts.ports import UserPreferenceRepository
from doktok_core.audit.logger import actor_identity
from fastapi import APIRouter, Depends, Response, status

from doktok_api.dependencies import Tenant, get_user_preference_repository

router = APIRouter(prefix="/api/v1/preferences", tags=["preferences"])

Prefs = Annotated[UserPreferenceRepository, Depends(get_user_preference_repository)]


@router.get("")
def get_preferences(tenant: Tenant, prefs: Prefs) -> dict[str, Any]:
    """All of the caller's stored preferences as a ``{key: value}`` map."""
    return prefs.get_all(tenant.tenant_id, actor_identity(tenant))


@router.put("")
def set_preferences(body: dict[str, Any], tenant: Tenant, prefs: Prefs) -> dict[str, Any]:
    """Merge the given ``{key: value}`` map into the caller's preferences and return the full set.
    Partial: keys not present in ``body`` are left unchanged (use DELETE to remove one)."""
    subject = actor_identity(tenant)
    prefs.set_many(tenant.tenant_id, subject, body)
    return prefs.get_all(tenant.tenant_id, subject)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(key: str, tenant: Tenant, prefs: Prefs) -> Response:
    """Remove one preference (idempotent)."""
    prefs.delete(tenant.tenant_id, actor_identity(tenant), key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
