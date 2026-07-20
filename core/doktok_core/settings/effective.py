"""Per-tenant model-stack resolution (epic #708, T1).

``effective(tenant)`` per purpose = **tenant override -> global saved settings -> env defaults**:
the tenant admin owns their stack in the UI, the console (static host token) owns the
deployment-global layer, and the config file defines the built-in defaults. Embedding and OCR are
deliberately NOT on the tenant path (the embedding model pins the index dimension; OCR engine +
parallelism size the host worker pool).

``no_egress`` is per-tenant with the same layering; the host lock keeps the ultimate kill switch.
"""

from __future__ import annotations

from typing import cast

from doktok_contracts.ports import AppSettingsRepository
from doktok_contracts.schemas import AiPurposeSettings, AiSettings

from doktok_core.config import Settings
from doktok_core.security.egress import effective_no_egress
from doktok_core.settings.bootstrap import env_default_ai_settings

# The tenant-overridable LLM purposes (embedding + OCR stay deployment-global).
_PURPOSES = ("pipeline", "rag", "ner", "keg", "rerank")


def effective_ai_settings(repo: AppSettingsRepository, tenant_id: str, env: Settings) -> AiSettings:
    """The tenant's effective AI model stack (epic #708, T1). Per purpose: the tenant's own
    override when set, else the console-saved global settings when any, else the env defaults."""
    env_defaults = env_default_ai_settings(env)
    global_saved = repo.get_ai_settings() if repo.has_ai_settings() else None
    override = repo.get_tenant_ai_settings(tenant_id)

    def pick(purpose: str) -> AiPurposeSettings:
        if override is not None:
            chosen = cast("AiPurposeSettings | None", getattr(override, purpose))
            if chosen is not None:
                return chosen
        if global_saved is not None:
            return cast(AiPurposeSettings, getattr(global_saved, purpose))
        return cast(AiPurposeSettings, getattr(env_defaults, purpose))

    resolved = {purpose: pick(purpose) for purpose in _PURPOSES}
    # Embedding is deployment-global: the tenant override never touches it.
    return AiSettings(**resolved, embedding=(global_saved or env_defaults).embedding)


def effective_tenant_no_egress(repo: AppSettingsRepository, tenant_id: str, env: Settings) -> bool:
    """The tenant's egress posture (epic #708): the host lock forces ON; else the tenant's stored
    value, then the global stored value, then the env default (ON = data stays on host)."""
    if env.no_egress_lock:
        return True
    override = repo.get_tenant_ai_settings(tenant_id)
    if override is not None and override.no_egress is not None:
        return override.no_egress
    return effective_no_egress(repo.get_no_egress(), env_default=env.no_egress, lock=False)
