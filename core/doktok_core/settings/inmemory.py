"""In-memory app-settings repository for tests/dev (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

from doktok_contracts.schemas import AiSettings, OcrSettings


class InMemoryAppSettingsRepository:
    def __init__(self) -> None:
        self._ai = AiSettings()
        self._ai_set = False
        self._ocr = OcrSettings()
        self._openai_key = ""
        self._heartbeat: datetime | None = None
        self._maintenance = False

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

    def get_ocr_settings(self) -> OcrSettings:
        return self._ocr.model_copy(deep=True)

    def set_ocr_settings(self, settings: OcrSettings) -> None:
        self._ocr = settings.model_copy(deep=True)

    def set_worker_heartbeat(self) -> None:
        self._heartbeat = datetime.now(UTC)

    def get_worker_heartbeat(self) -> datetime | None:
        return self._heartbeat

    def set_maintenance_mode(self, *, enabled: bool) -> None:
        self._maintenance = enabled

    def get_maintenance_mode(self) -> bool:
        return self._maintenance
