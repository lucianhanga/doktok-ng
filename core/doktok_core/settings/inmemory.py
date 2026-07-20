"""In-memory app-settings repository for tests/dev (no DB)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from doktok_contracts.schemas import AiSettings, OcrSettings, TenantAiSettings


class InMemoryAppSettingsRepository:
    def __init__(self) -> None:
        self._ai = AiSettings()
        self._ai_set = False
        self._ocr = OcrSettings()
        self._tenant_ai: dict[str, TenantAiSettings] = {}
        self._tenant_openai_keys: dict[str, str] = {}
        self._openai_key = ""
        self._no_egress: bool | None = None
        self._heartbeat: datetime | None = None
        self._maintenance = False
        self.backup_status: dict[str, dict[str, object]] | None = None  # DRP test fixture (#368)
        # DRP history test fixture (M12 DRP hardening): ordered OLDEST-first JSONL lines (raw
        # strings) exactly as the host appends them, so the in-memory reader exercises the same
        # parse/chain logic as the file-backed Postgres reader. None = no history source available.
        self.backup_history_lines: list[str] | None = None
        # F-41 (#653): the anchored history head (mirrors the Postgres app_settings KV anchor).
        self.history_anchor: dict[str, object] | None = None

    def get_ai_settings(self) -> AiSettings:
        return self._ai.model_copy(deep=True)

    def set_ai_settings(self, settings: AiSettings) -> None:
        self._ai = settings.model_copy(deep=True)
        self._ai_set = True

    def has_ai_settings(self) -> bool:
        return self._ai_set

    def get_openai_api_key(self) -> str:
        return self._openai_key

    def set_openai_api_key(self, key: str) -> None:
        self._openai_key = key

    def get_no_egress(self) -> bool | None:
        return self._no_egress

    def set_no_egress(self, value: bool) -> None:
        self._no_egress = value

    def get_ocr_settings(self) -> OcrSettings:
        return self._ocr.model_copy(deep=True)

    def set_ocr_settings(self, settings: OcrSettings) -> None:
        self._ocr = settings.model_copy(deep=True)

    def get_tenant_ai_settings(self, tenant_id: str) -> TenantAiSettings | None:
        override = self._tenant_ai.get(tenant_id)
        return override.model_copy(deep=True) if override is not None else None

    def set_tenant_ai_settings(self, tenant_id: str, settings: TenantAiSettings) -> None:
        self._tenant_ai[tenant_id] = settings.model_copy(deep=True)

    def delete_tenant_ai_settings(self, tenant_id: str) -> None:
        self._tenant_ai.pop(tenant_id, None)
        self._tenant_openai_keys.pop(tenant_id, None)  # a full reset drops the tenant key (#719)

    def get_tenant_openai_api_key(self, tenant_id: str) -> str:
        return self._tenant_openai_keys.get(tenant_id, "")

    def set_tenant_openai_api_key(self, tenant_id: str, key: str) -> None:
        self._tenant_openai_keys[tenant_id] = key

    def set_worker_heartbeat(self) -> None:
        self._heartbeat = datetime.now(UTC)

    def get_worker_heartbeat(self) -> datetime | None:
        return self._heartbeat

    def set_maintenance_mode(self, *, enabled: bool) -> None:
        self._maintenance = enabled

    def get_maintenance_mode(self) -> bool:
        return self._maintenance

    def get_backup_status(self) -> dict[str, dict[str, object]] | None:
        return self.backup_status

    def get_backup_history(
        self, limit: int = 100, leg: str | None = None
    ) -> tuple[list[dict[str, object]], bool, bool, bool]:
        # Mirror the Postgres file reader's contract over the in-memory ``backup_history_lines``
        # fixture: parse oldest-first JSONL, verify the prev_sha256 chain, filter leg, newest-first.
        if self.backup_history_lines is None:
            return ([], False, False, True)
        lines = [ln for ln in self.backup_history_lines if ln.strip()]
        if not lines:
            return ([], False, False, True)
        integrity_ok = True
        for i in range(1, len(lines)):
            try:
                claimed = json.loads(lines[i]).get("prev_sha256", "")
            except json.JSONDecodeError:
                integrity_ok = False
                continue
            actual = hashlib.sha256(lines[i - 1].encode("utf-8")).hexdigest()
            if claimed != actual:
                integrity_ok = False
                break
        # F-41 (#653): the sha chain covers only this window - the anchored head catches a fully
        # rewritten file (head inevitably changes) and a silently deleted tail (head regresses).
        from doktok_core.backup.history_anchor import anchor_check

        anchor_ok, new_anchor = anchor_check(lines, self.history_anchor)
        if anchor_ok and integrity_ok:
            self.history_anchor = new_anchor
        integrity_ok = integrity_ok and anchor_ok
        events: list[dict[str, object]] = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            if leg is not None and rec.get("leg") != leg:
                continue
            events.append(rec)
        events.reverse()
        return (events[: max(0, limit)], True, False, integrity_ok)
