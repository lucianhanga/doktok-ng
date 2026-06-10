"""Typed application settings loaded from the environment.

All settings use the ``DOKTOK_`` prefix and have safe, local-first defaults (ADR-0003, ADR-0006).
See .env.example and brief section 24.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOKTOK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = "postgresql://doktok:doktok@localhost:5432/doktok"
    files_root: str = "./storage/files"

    default_model: str = "qwen3.6:35b-a3b"
    embedding_model: str = "mxbai-embed-large:latest"
    ollama_base_url: str = "http://localhost:11434"

    no_egress: bool = True

    max_file_mb: int = 200
    max_pages: int = 500
    file_stability_seconds: int = 3


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
