"""The typed environment registry: every variable a deployed process reads, validated once.

``Settings`` is the single source of truth for environment variables. Each field records its
purpose, whether it is a secret, and the profiles (``APP_ENV``) that require or forbid it;
``docs/ENVIRONMENT.md`` is generated from these fields (``scripts/gen_env_docs.py``) and a test
fails if any module reads a variable that is not registered here. ``load_settings`` validates the
whole environment and raises ``EnvironmentInvalid`` naming every problem at once (names only, never
values), so a misconfigured process stops at startup instead of on first use.

Profile rules (beyond per-field types):

* ``dev`` and ``test`` need a password database URL; ``staging`` and ``production`` reach Cloud SQL
  through the connector with IAM auth, so they need the instance, IAM user and database name.
* cloud profiles refuse local-only switches (unauthenticated tasks, local payload directory) and
  a plain ``ANTHROPIC_API_KEY``: the model key comes from Secret Manager.
* the staff identity provider is all-or-nothing (issuer, audience and JWKS together).

GUARANTEE(test_every_env_read_is_registered): static scan of ``src``.
GUARANTEE(test_cloud_profile_refuses_local_switches): profile rules.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Versioned YAML assets (grading weights, routing, checklist). The default resolves to the
# repository's config/ directory; images that install the package non-editably set CONFIG_DIR.
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", Path(__file__).resolve().parents[2] / "config"))

Profile = Literal["dev", "test", "staging", "production"]
LOCAL: tuple[Profile, ...] = ("dev", "test")
CLOUD: tuple[Profile, ...] = ("staging", "production")
# Never connected in the cloud: the lifespan binds the Cloud SQL connector engine before any
# request is served (``db.session.bind_engine``). Creating an engine does not open a connection.
UNBOUND_DATABASE_URL = "postgresql+asyncpg://unbound@localhost:1/unbound"


def var(
    default: Any = None,
    *,
    purpose: str,
    secret: bool = False,
    required_in: tuple[Profile, ...] = (),
    forbidden_in: tuple[Profile, ...] = (),
    component: str = "api, worker",
) -> Any:
    """A registered variable: its documentation travels with its type."""
    return Field(
        default,
        description=purpose,
        json_schema_extra={
            "secret": secret,
            "required_in": list(required_in),
            "forbidden_in": list(forbidden_in),
            "component": component,
        },
    )


class EnvironmentInvalid(RuntimeError):
    """The environment fails the registry; the message lists variable names only."""


class Settings(BaseSettings):
    """Every environment variable of the API, worker and run job (see the module docstring)."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True, case_sensitive=False)

    # process
    app_env: Profile = var("dev", purpose="Runtime profile; selects adapters and profile rules.")
    service_role: Literal["api", "worker", "job", "migrate"] = var(
        "api", purpose="Which entry point this container serves (set by the deployment)."
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = var(
        "INFO", purpose="Minimum level of the structured JSON logs."
    )
    port: int = var(8080, purpose="HTTP port the API listens on (Cloud Run injects it).")
    config_dir: str | None = var(purpose="Directory of the versioned YAML config (image default).")

    # database
    database_url: SecretStr | None = var(
        purpose="Async SQLAlchemy URL of the NOBYPASSRLS runtime role (local profiles only).",
        secret=True,
        required_in=LOCAL,
    )
    checkpoint_database_url: SecretStr | None = var(
        purpose="psycopg URL for the run checkpointer, direct to the writer (no pooler).",
        secret=True,
        required_in=("dev", "staging", "production"),
    )
    db_pool_size: int = var(10, purpose="Runtime connection pool size per instance.")
    db_max_overflow: int = var(5, purpose="Extra connections allowed above the pool size.")
    cloud_sql_instance: str | None = var(
        purpose="Cloud SQL connection name (project:region:instance).", required_in=CLOUD
    )
    db_iam_user: str | None = var(
        purpose="IAM database user of the runtime service account.", required_in=CLOUD
    )
    db_name: str | None = var(purpose="Application database name.", required_in=CLOUD)
    migrator_database_url: SecretStr | None = var(
        purpose="Owner-role URL used only by the migration step.",
        secret=True,
        component="migrate",
    )
    app_rw_password: SecretStr | None = var(
        purpose="Password the migrations set on the app_rw runtime role (migration step only).",
        secret=True,
        component="migrate",
    )

    # tenant API tokens
    jwks_url: str = var(
        "http://localhost:9/jwks.json",
        purpose="JWKS endpoint that signs tenant API bearer tokens.",
        required_in=CLOUD,
    )
    jwt_audience: str = var(
        "ai-saas", purpose="Expected audience of tenant bearer tokens.", required_in=CLOUD
    )
    jwt_issuer: str = var(
        "https://idp.example.com/",
        purpose="Expected issuer of tenant bearer tokens.",
        required_in=CLOUD,
    )

    # staff (operator) identity plane: all three or none
    staff_jwt_issuer: str | None = var(purpose="Staff IdP issuer (Google Workspace).")
    staff_jwt_audience: str | None = var(purpose="Staff OAuth client id expected as audience.")
    staff_jwks_url: str | None = var(purpose="Staff IdP JWKS endpoint.")
    staff_workspace_domain: str | None = var(
        purpose="Workspace domain staff accounts must belong to.", component="api, web"
    )
    staff_operator_emails: str = var("", purpose="Comma-separated operator allowlist.")
    staff_support_emails: str = var("", purpose="Comma-separated support allowlist.")

    # run limits
    run_budget_usd: float = var(5.0, purpose="Per-run spend ceiling in USD (all phases).")
    run_tool_call_cap: int = var(60, purpose="Per-run connector call ceiling.")
    run_reserve_credits: float = var(6.0, purpose="Credits reserved when a run starts.")

    # Google Cloud runtime
    gcp_project: str | None = var(purpose="Google Cloud project id.", required_in=CLOUD)
    gcp_region: str | None = var(purpose="Region of Cloud Run, Tasks and SQL.", required_in=CLOUD)
    tasks_queue: str | None = var(purpose="Cloud Tasks queue for run slices.", required_in=CLOUD)
    worker_url: str | None = var(purpose="HTTPS URL of the worker service.", required_in=CLOUD)
    tasks_service_account: str | None = var(
        purpose="Service account Cloud Tasks signs OIDC tokens as.", required_in=CLOUD
    )
    tasks_audience: str | None = var(
        purpose="Audience the worker expects on task tokens.", required_in=CLOUD
    )
    payload_bucket: str | None = var(
        purpose="GCS bucket for raw payloads and exports (per-tenant prefixes).",
        required_in=CLOUD,
    )
    redis_host: str | None = var(
        purpose="Memorystore host for the shared rate limiter.", required_in=CLOUD
    )
    redis_url: str | None = var(
        purpose=(
            "Redis URL (redis:// or rediss://) for the research cache index: a fast key to"
            " reference lookup over finished provider calls. Optional; without it the cache"
            " reads Postgres only."
        )
    )
    dev_assume_provider_balance: str | None = var(
        purpose="Local stack only: treat every provider balance as funded (unattended runs).",
        forbidden_in=CLOUD,
        component="worker",
    )
    dev_auto_approve_caps: str | None = var(
        purpose="Local stack only: approve run caps as tenant policy without a person.",
        forbidden_in=CLOUD,
        component="worker",
    )
    next_dist_dir: str | None = var(
        purpose="Web build: Next.js output directory override (parity builds).", component="web"
    )
    parity_web_origin: str | None = var(
        purpose="Web tests: origin of the web app the parity suite drives.", component="web"
    )
    screens: str | None = var(
        purpose="Web tests: comma separated screen ids the screenshot suite captures.",
        component="web",
    )
    run_sweeper: Literal["on", "off"] = var(
        "on",
        purpose=(
            "Worker: end runs whose worker died (lease expired for ORPHAN_AFTER_S) PARTIAL"
            " worker_lost at startup and every SWEEP_INTERVAL_S; off disables the sweeper."
        ),
        component="worker",
    )
    orphan_after_s: int = var(
        600, purpose="Seconds a run lease must be expired before the sweeper ends the run."
    )
    sweep_interval_s: int = var(300, purpose="Seconds between orphan sweeps in the worker.")
    research_cache: Literal["on", "off"] = var(
        "on",
        purpose=(
            "Reuse finished provider calls and model replies across runs of one tenant"
            " (config/research_cache.yaml). off: every run pays for every call."
        ),
    )
    document_scanner: Literal["signature", "clamd"] = var(
        "signature",
        purpose=(
            "Malware scan of uploaded documents before parsing: signature (built-in test and"
            " structure signatures, local profiles only) or clamd (CLAMD_HOST). In staging and"
            " production anything but clamd fails closed: every upload is rejected unscanned."
        ),
    )
    clamd_host: str | None = var(purpose="Host of the clamd daemon that scans uploads.")
    clamd_port: int = var(3310, purpose="TCP port of the clamd daemon.")
    audio_transcription: Literal["off"] = var(
        "off",
        purpose=(
            "Speech to text with speaker labels for uploaded audio. Only off exists until a"
            " vendor is chosen: audio uploads are rejected with a clear message."
        ),
    )
    public_base_url: str | None = var(
        purpose="Public HTTPS origin of the web app (OIDC redirects, links).", required_in=CLOUD
    )
    llm_access: Literal["anthropic_api", "vertex_ai"] = var(
        "anthropic_api", purpose="How the model provider is reached (decision D-19)."
    )
    vertex_region: str | None = var(purpose="Vertex AI region when LLM_ACCESS=vertex_ai.")
    model_router: Literal["on", "off"] = var(
        "off", purpose="Route model calls through the recorded router (legacy switch)."
    )
    anthropic_secret: str = var(
        "anthropic-api-key", purpose="Secret Manager secret id holding the model API key."
    )
    byok_kms_key: str | None = var(
        purpose="Cloud KMS crypto-key resource for optional encrypted tenant model keys."
    )

    # local development switches
    anthropic_api_key: SecretStr | None = var(
        purpose="Model API key for local runs (cloud reads it from Secret Manager).",
        secret=True,
        forbidden_in=CLOUD,
    )
    payload_dir: str | None = var(
        purpose="Local directory for raw payloads (memory store when unset).", forbidden_in=CLOUD
    )
    connectors: Literal["on", "off"] = var(
        "off", purpose="Enable live provider connectors in dev (cloud always wires them)."
    )
    allow_unauthenticated_tasks: Literal["0", "1"] = var(
        "0", purpose="Accept unsigned task calls (local stack only).", forbidden_in=CLOUD
    )
    llm_fake: Literal["hermetic"] | None = var(
        purpose="Select the deterministic fake model (hermetic dev and test only).",
        forbidden_in=CLOUD,
    )
    fake_model_scenario: str | None = var(
        purpose="Fake model scenario file under config/hermetic/fake_model (default happy).",
        forbidden_in=CLOUD,
    )
    p_search_api_key: SecretStr | None = var(purpose="Web search provider key.", secret=True)
    p_scrape_api_key: SecretStr | None = var(purpose="Page scrape provider key.", secret=True)
    p_signals_api_key: SecretStr | None = var(purpose="Signals provider key.", secret=True)
    p_signals_api_token: SecretStr | None = var(purpose="Signals provider token.", secret=True)
    p_people_api_key: SecretStr | None = var(purpose="People provider key.", secret=True)
    p_org_api_key: SecretStr | None = var(purpose="Organisation provider key.", secret=True)

    # exports
    export_renderer: str | None = var(purpose="Word renderer override (see exports.wiring).")
    export_render_checks: Literal["on", "off"] = var(
        "off", purpose="Run LibreOffice render checks on every export."
    )

    @model_validator(mode="after")
    def _profile_rules(self) -> Settings:
        problems: list[str] = []
        for name, field in type(self).model_fields.items():
            extra = field.json_schema_extra
            assert isinstance(extra, dict)
            required, forbidden = extra["required_in"], extra["forbidden_in"]
            assert isinstance(required, list) and isinstance(forbidden, list)
            value = getattr(self, name)
            present = value is not None and value != ""
            if self.app_env in required and not present:
                problems.append(f"{name.upper()} is required for APP_ENV={self.app_env}")
            if self.app_env in forbidden and present and value != field.default:
                problems.append(f"{name.upper()} must not be set for APP_ENV={self.app_env}")
        staff = (self.staff_jwt_issuer, self.staff_jwt_audience, self.staff_jwks_url)
        if any(staff) and not all(staff):
            problems.append("STAFF_JWT_ISSUER, STAFF_JWT_AUDIENCE and STAFF_JWKS_URL go together")
        if self.app_env in CLOUD and self.llm_access == "vertex_ai" and not self.vertex_region:
            problems.append("VERTEX_REGION is required when LLM_ACCESS=vertex_ai")
        for url_field in ("worker_url", "public_base_url"):
            url = getattr(self, url_field)
            if self.app_env in CLOUD and url and not url.startswith("https://"):
                problems.append(f"{url_field.upper()} must be https in {self.app_env}")
        if self.database_url is not None and not self.database_url.get_secret_value().startswith(
            "postgresql+asyncpg://"
        ):
            problems.append("DATABASE_URL must use the postgresql+asyncpg driver")
        for positive in ("db_pool_size", "run_budget_usd", "run_tool_call_cap", "port"):
            if getattr(self, positive) <= 0:
                problems.append(f"{positive.upper()} must be positive")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def runtime_database_url(self) -> str:
        """The URL the default engine is created with (unbound placeholder in the cloud)."""
        if self.database_url is None:
            return UNBOUND_DATABASE_URL
        return self.database_url.get_secret_value()


class _Explicit(Settings):
    """Settings validated from an explicit mapping only (the process environment is ignored)."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Only the constructor arguments."""
        return (init_settings,)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Validate ``env`` (default: the process environment); names, never values, on failure."""
    try:
        if env is None:
            return Settings()
        known = {k.lower(): v for k, v in env.items() if k.lower() in Settings.model_fields}
        return _Explicit.model_validate(known)
    except ValidationError as exc:
        names = []
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            loc = ".".join(str(p) for p in error["loc"]).upper() or "ENVIRONMENT"
            names.append(f"{loc}: {error['msg']}")
        raise EnvironmentInvalid("invalid environment: " + " | ".join(names)) from None


@lru_cache
def get_settings() -> Settings:
    """The cached, validated settings for this process."""
    return load_settings()


def registry() -> list[dict[str, Any]]:
    """Registered variables with their documentation (for docs and the doctor)."""
    rows = []
    for name, field in Settings.model_fields.items():
        extra = field.json_schema_extra
        assert isinstance(extra, dict)
        rows.append({"name": name.upper(), "purpose": field.description, **extra})
    return rows


def main() -> int:
    """``python -m account_intelligence.config``: validate the current environment."""
    import sys

    try:
        s = load_settings()
    except EnvironmentInvalid as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print(f"PASS environment valid for APP_ENV={s.app_env} ({len(registry())} variables)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
