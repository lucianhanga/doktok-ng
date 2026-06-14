"""In-memory app-settings repository for tests/dev (no DB)."""

from __future__ import annotations

from doktok_contracts.schemas import AiSettings, OcrSettings


class InMemoryAppSettingsRepository:
    def __init__(self) -> None:
        self._ai = AiSettings()
        self._ocr = OcrSettings()
        self._openai_key = ""

    def get_ai_settings(self) -> AiSettings:
        return self._ai.model_copy(deep=True)

    def set_ai_settings(self, settings: AiSettings) -> None:
        self._ai = settings.model_copy(deep=True)

    def get_openai_api_key(self) -> str:
        return self._openai_key

    def set_openai_api_key(self, key: str) -> None:
        self._openai_key = key

    def get_ocr_settings(self) -> OcrSettings:
        return self._ocr.model_copy(deep=True)

    def set_ocr_settings(self, settings: OcrSettings) -> None:
        self._ocr = settings.model_copy(deep=True)
