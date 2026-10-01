"""Runtime settings, read from environment variables only (roadmap s11.4 secrets and keys).

No dotenv file is read: local development exports variables (docker compose passes them in),
and deployed environments inject them from the secrets manager. Keys are SecretStr so they never
print in logs or reprs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "production"]

# Keys a live Phase 1 run needs. Phase 0 runs fully offline with none of them set.
LIVE_KEYS: dict[str, str] = {
    "ANTHROPIC_API_KEY": "model provider (models.yaml profiles with provider: anthropic)",
    "PARALLEL_API_KEY": "web search and page reads",
    "FIRECRAWL_API_KEY": "job boards, browser pages, transcripts",
    "PREDICTLEADS_API_KEY": "structured news, technology detections, postings",
    "PREDICTLEADS_API_TOKEN": "second credential the structured signals broker issues",
    "BITSCALE_API_KEY": "people by title, profile enrichment",
    "SUMBLE_API_KEY": "people by technology, organizations, tech stack",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    env: Environment = "dev"
    log_level: str = "INFO"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "account-intelligence"

    # "fake" keeps every model call offline; "live" is refused until Phase 1 adapters exist.
    model_mode: Literal["fake", "live"] = "fake"
    broker_mode: Literal["recorded", "live"] = "recorded"

    anthropic_api_key: SecretStr | None = None
    parallel_api_key: SecretStr | None = None
    firecrawl_api_key: SecretStr | None = None
    predictleads_api_key: SecretStr | None = None
    predictleads_api_token: SecretStr | None = None
    bitscale_api_key: SecretStr | None = None
    sumble_api_key: SecretStr | None = None

    database_url: SecretStr | None = Field(default=None)

    def missing_live_keys(self) -> list[str]:
        """Environment variable names still unset for a live run."""
        return [name for name in LIVE_KEYS if getattr(self, name.lower()) is None]


def get_settings() -> Settings:
    return Settings()
