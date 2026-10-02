"""Runtime settings, read once from the environment (see .env.example)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Versioned YAML assets (grading weights, routing, checklist). The default resolves to the
# repository's config/ directory; images that install the package non-editably set CONFIG_DIR.
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", Path(__file__).resolve().parents[2] / "config"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    env: Literal["dev", "staging", "prod"] = "dev"

    # Runtime role app_rw: not the table owner and NOBYPASSRLS.
    database_url: str
    checkpoint_database_url: str  # psycopg URL, direct to the writer (no txn-mode proxy)
    db_pool_size: int = 10
    db_max_overflow: int = 5

    jwks_url: str
    jwt_audience: str
    jwt_issuer: str

    run_budget_usd: float = 5.0
    slice_seconds: int = 240
    max_slices: int = 60  # 60 x 240 s = 4 h hard ceiling per run
    tenant_concurrency: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
