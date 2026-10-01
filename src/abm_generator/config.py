"""Typed configuration loaded from environment (pydantic-settings). No secrets in code."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings; every value comes from the environment or .env."""

    database_url: str = "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
    migrator_database_url: str = "postgresql://postgres:dev@localhost:5432/ai"
    checkpoint_database_url: str = "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
    db_pool_size: int = 5
    db_max_overflow: int = 0
    db_statement_timeout_ms: int = 30_000
    slice_seconds: int = 240
    max_slices: int = 60
    log_level: str = "INFO"
    env: str = "dev"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
