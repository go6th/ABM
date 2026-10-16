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
# Every file under config/ is found through CONFIG_DIR or config_dir(), never relative to a
# module's own file (an installed package has no config/ beside it; guard test
# test_no_module_finds_repository_files_relative_to_its_own_file).
_SOURCE_CONFIG = Path(__file__).resolve().parents[2] / "config"
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", _SOURCE_CONFIG))
# The research skill package (``replica.jobs`` readiness words, the console's "skill installed").
SKILL_PACKAGE_DIR = CONFIG_DIR / "skills" / "abm-account-research"


def config_dir() -> Path:
    """CONFIG_DIR as the environment says now (tests point it elsewhere after import)."""
    return Path(os.environ.get("CONFIG_DIR") or _SOURCE_CONFIG)


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
    k_service: str | None = var(
        purpose="Set by Cloud Run on services: a dev or test profile is refused when present.",
        component="api, worker",
    )
    cloud_run_job: str | None = var(
        purpose="Set by Cloud Run on jobs: a dev or test profile is refused when present.",
        component="job, migrate",
    )
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
    # Connection budget (docs/deploy/agents/backend-hardening.md): every process holds at most
    # DB_POOL_SIZE + DB_MAX_OVERFLOW engine connections (the Cloud SQL engine too) and a worker one
    # checkpointer connection per in-flight slice (concurrency 4). Production at every maximum
    # with the stack's 5 + 5: api 10 x 10 + worker 8 x (10 + 4) + jobs 2 x 5 + 15 reserved
    # (operators, Query Insights, maintenance) = 237 <= 400 max_connections (db-custom-2-7680);
    # staging 3 x 10 + 2 x 14 + 10 + 15 = 83 <= 200. Raising either value needs that sum redone.
    db_pool_size: int = var(
        10,
        purpose="Engine connections kept per process (the stack sets 5; the connection budget is"
        " max instances x (DB_POOL_SIZE + DB_MAX_OVERFLOW), see the registry comment).",
    )
    db_max_overflow: int = var(
        5,
        purpose="Extra engine connections per process above DB_POOL_SIZE (the stack sets 5).",
    )
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
    app_rw_iam_users: str | None = var(
        purpose="Comma-separated Cloud SQL IAM database users that migration 0071 makes members of"
        " app_rw (nothing else); unset: no grant.",
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
    staff_external_emails: str = var(
        "",
        purpose="Personal Google accounts (no Workspace) allowed as staff; still need a role.",
        component="api, web",
    )

    # run limits
    run_budget_usd: float = var(5.0, purpose="Per-run spend ceiling in USD (all phases).")
    run_tool_call_cap: int = var(60, purpose="Per-run connector call ceiling.")
    ask_whole_file_max_chars: int = var(
        300_000,
        purpose=(
            "Ask: the uploaded file a question is about goes to the model whole when it has at"
            " most this many characters (about 75k tokens); a larger file answers through"
            " retrieved passages only."
        ),
        component="api",
    )
    run_reserve_credits: float = var(6.0, purpose="Credits reserved when a run starts.")

    # Google Cloud runtime
    gcp_project: str | None = var(purpose="Google Cloud project id.", required_in=CLOUD)
    gcp_region: str | None = var(purpose="Region of Cloud Run, Tasks and SQL.", required_in=CLOUD)
    tasks_queue: str | None = var(
        purpose="Cloud Tasks queue for run slices and approval timeouts.", required_in=CLOUD
    )
    tasks_queue_documents: str | None = var(
        purpose="Cloud Tasks queue for document processing (unset: TASKS_QUEUE)."
    )
    tasks_queue_jobs: str | None = var(
        purpose="Cloud Tasks queue for research jobs and Download everything (unset: TASKS_QUEUE)."
    )
    worker_url: str | None = var(purpose="HTTPS URL of the worker service.", required_in=CLOUD)
    tasks_service_account: str | None = var(
        purpose="Service account Cloud Tasks signs OIDC tokens as.", required_in=CLOUD
    )
    tasks_audience: str | None = var(
        purpose="Audience the worker expects on task tokens.", required_in=CLOUD
    )
    blob_mount_dir: str | None = var(
        purpose="Read-only mount of the payload bucket on the worker (/mnt/blobs); the parser"
        " sandbox reads documents there instead of a whole-file copy in memory. Required for the"
        " worker in the cloud.",
        component="worker",
    )
    payload_bucket: str | None = var(
        purpose="GCS bucket for raw payloads and exports (per-tenant prefixes).",
        required_in=CLOUD,
    )
    redis_url: str | None = var(
        purpose=(
            "Redis URL (redis:// locally, rediss:// for Memorystore with TLS): the shared rate"
            " limiters (sign-in and providers) and the research cache index. Unset: in-process"
            " rate buckets and Postgres-only cache lookups."
        ),
        required_in=CLOUD,
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
    moonshot_base_url: str = var(
        "https://api.moonshot.ai/v1",
        purpose="Kimi K3 API base URL (https): Moonshot AI, or https://ollama.com/v1 (Ollama).",
    )
    moonshot_api_dialect: Literal["moonshot", "ollama"] = var(
        "moonshot",
        purpose="Which Kimi K3 API MOONSHOT_BASE_URL serves: moonshot, or ollama (Ollama Cloud).",
    )
    moonshot_model: str | None = var(
        purpose="The provider's own Kimi K3 model name when it differs (Ollama: kimi-k3:cloud).",
    )
    gmail_api_base: str = var(
        "https://gmail.googleapis.com/gmail/v1",
        purpose="Gmail API base URL for the per-user Mail and drive sync.",
        component="worker",
    )
    google_drive_api_base: str = var(
        "https://www.googleapis.com/drive/v3",
        purpose="Google Drive API base URL for the per-user Mail and drive sync.",
        component="worker",
    )
    microsoft_graph_api_base: str = var(
        "https://graph.microsoft.com/v1.0",
        purpose="Microsoft Graph API base URL (Outlook, OneDrive, SharePoint sync).",
        component="worker",
    )
    moonshot_secret: str | None = var(
        purpose="Secret Manager secret id holding the Kimi K3 (Moonshot AI) key; unset: not set up."
    )
    csrf_key_secret: str = var(
        "csrf-signing-key",
        purpose="Secret Manager secret id of the CSRF signing key (32 or more random bytes).",
        component="api",
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
    moonshot_api_key: SecretStr | None = var(
        purpose="Kimi K3 (Moonshot AI) key for local runs (cloud reads it from Secret Manager).",
        secret=True,
        forbidden_in=CLOUD,
    )
    moonshot_api_key_file: str | None = var(
        purpose="File holding the Kimi K3 key for local runs.", forbidden_in=CLOUD
    )
    payload_dir: str | None = var(
        purpose="Local directory for raw payloads (memory store when unset).", forbidden_in=CLOUD
    )
    devstore_public_url: str | None = var(
        purpose=(
            "Dev only: the browser-facing URL of the development object store (resumable"
            " uploads straight to storage, owner decision 2026-10-14); unset: the API's"
            " content route stands in."
        ),
        forbidden_in=CLOUD,
    )
    devstore_root: str | None = var(
        purpose="Dev only: the directory the development object store writes (the blob root).",
        forbidden_in=CLOUD,
        component="objectstore",
    )
    devstore_allow_origin: str | None = var(
        purpose="Dev only: comma-separated web origins the development object store answers.",
        forbidden_in=CLOUD,
        component="objectstore",
    )
    devstore_session_ttl_s: int = var(
        172800,
        purpose="Dev only: how long a development object store upload session lives (seconds).",
        forbidden_in=CLOUD,
        component="objectstore",
    )
    connectors: Literal["on", "off", "hermetic"] = var(
        "off",
        purpose=(
            "Provider connectors in dev: off, on (live providers, needs keys) or hermetic"
            " (synthetic replies, no outside call; dev and test only). Cloud always wires the"
            " live ones."
        ),
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

    # document retrieval vectors from an on-server transformer (owner decision 2026-10-15)
    document_semantic_embedder: Literal["fastembed"] | None = var(
        purpose="Set to fastembed to index and search documents by meaning (on-server model).",
        component="api, worker",
    )
    fastembed_cache_path: str | None = var(
        purpose="Directory holding the baked-in embedding model files (no run-time download).",
        component="api, worker",
    )
    # application metrics for the dashboards and alerts (audit 2026-10-15)
    metrics_export: Literal["cloud_monitoring"] | None = var(
        purpose="Set to cloud_monitoring to send app metrics to Cloud Monitoring (unset: none).",
        component="api, worker",
    )

    # transactional email (invites, email verification, password reset, alert email) over SMTP;
    # unset SMTP_HOST keeps mail fail closed (owner decision 2026-10-15)
    smtp_host: str | None = var(purpose="SMTP server, e.g. smtp.gmail.com (unset: no email).")
    smtp_port: int = var(587, purpose="SMTP port: 587 STARTTLS or 465 implicit TLS.")
    smtp_username: str | None = var(purpose="SMTP login, e.g. the sending Gmail address.")
    smtp_password: SecretStr | None = var(
        purpose="SMTP password or app password (cloud: injected from Secret Manager).",
        secret=True,
    )
    mail_from: str | None = var(purpose="From address of outgoing email (default: SMTP login).")
    mail_from_name: str | None = var(purpose="From name of outgoing email.")

    # exports
    # Mail and drive (per-user, read-only): the OAuth apps the owner registers. Until both the
    # id and the secret of a provider are set, its sources say "Not available yet".
    maildrive_google_client_id: str | None = var(
        purpose="Google OAuth app client id for Gmail and Google Drive (Mail and drive)."
    )
    maildrive_google_client_secret: SecretStr | None = var(
        purpose="Google OAuth app client secret (cloud: injected from Secret Manager).",
        secret=True,
    )
    maildrive_google_client_secret_file: str | None = var(
        purpose="Path to a file holding the Google client secret (instead of the variable)."
    )
    maildrive_microsoft_client_id: str | None = var(
        purpose="Microsoft OAuth app client id for Outlook, OneDrive and SharePoint."
    )
    maildrive_microsoft_client_secret: SecretStr | None = var(
        purpose="Microsoft OAuth app client secret (cloud: injected from Secret Manager).",
        secret=True,
    )
    maildrive_microsoft_client_secret_file: str | None = var(
        purpose="Path to a file holding the Microsoft client secret (instead of the variable)."
    )
    research_engine: Literal["graph", "hermetic", "skill"] | None = var(
        purpose=(
            "Research job engine: graph (the run lifecycle on the ABM agent graph; cloud"
            " default), hermetic (no outside call; dev and test default) or skill (the skill"
            " executor, refused until it is built)."
        ),
    )
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
        if self.app_env in CLOUD and self.connectors == "hermetic":
            problems.append(f"CONNECTORS: synthetic providers refused for APP_ENV={self.app_env}")
        for idp_field in ("jwks_url", "jwt_issuer", "staff_jwks_url", "staff_jwt_issuer"):
            url = getattr(self, idp_field) or ""
            host = url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].lower()
            local = not url.startswith("https://") or any(h in host for h in _LOCAL_HOSTS)
            if self.app_env in CLOUD and url and local:
                # the replica's fake IdP (or any local or plain-http one) signs nothing in the cloud
                problems.append(f"{idp_field.upper()} must be a real https identity provider")
        if self.app_env in CLOUD and self.llm_access == "vertex_ai" and not self.vertex_region:
            problems.append("VERTEX_REGION is required when LLM_ACCESS=vertex_ai")
        for url_field in (
            "worker_url",
            "public_base_url",
            "moonshot_base_url",
            "gmail_api_base",
            "google_drive_api_base",
            "microsoft_graph_api_base",
        ):
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


# Local identity providers and hosts a cloud profile must never trust (the replica's fake IdP).
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "fake-idp", "host.docker.internal", "[::1]")


def deployment_refusals(env: Mapping[str, str]) -> list[str]:
    """Rules on the raw environment that the typed fields cannot see (names only):

    * a dev or test profile on Cloud Run (``K_SERVICE`` for services, ``CLOUD_RUN_JOB`` for jobs,
      both set by the platform): those profiles turn on the dev staff verifier and unsigned task
      calls (security audit L13);
    * any ``DEVSTORE_*`` variable in staging or production (the development object store is
      local only), whatever its value, the defaults included.

    GUARANTEE(test_cloud_profile_refuses_local_switches)"""
    problems: list[str] = []
    profile = env.get("APP_ENV", "dev")
    if profile in LOCAL and (env.get("K_SERVICE") or env.get("CLOUD_RUN_JOB")):
        problems.append(f"APP_ENV={profile} is refused on Cloud Run (K_SERVICE or CLOUD_RUN_JOB)")
    if profile in CLOUD:
        devstore = sorted(k.upper() for k in env if k.upper().startswith("DEVSTORE_"))
        if devstore:
            problems.append(f"{', '.join(devstore)} must not be set for APP_ENV={profile}")
    return problems


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Validate ``env`` (default: the process environment); names, never values, on failure."""
    refusals = deployment_refusals(os.environ if env is None else env)
    if refusals:
        raise EnvironmentInvalid("invalid environment: " + " | ".join(refusals))
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
