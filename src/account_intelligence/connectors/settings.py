"""Typed view of config/providers.yaml. Loaded once per runtime, never mutated."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from account_intelligence.config import CONFIG_DIR
from account_intelligence.connectors.base import Operation, ProviderCode


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RetryCfg(_M):
    """Node RetryPolicy values from config (the single retry owner for broker 429 and 5xx)."""

    max_attempts: int = Field(ge=1, le=10)
    initial_interval_s: float = Field(gt=0)
    backoff_factor: float = Field(ge=1)
    max_interval_s: float = Field(gt=0)
    jitter: bool


class RateLimitDefaults(_M):
    """Pacing caps: maximum wait and cooldown."""

    max_wait_s: float
    max_cooldown_s: float
    default_cooldown_s: float


class BreakerCfg(_M):
    """Circuit breaker thresholds."""

    failure_threshold: int
    window_s: float
    cooldown_s: float
    half_open_probes: int


class PaginationCfg(_M):
    """Default pagination caps."""

    max_pages: int = Field(ge=1)
    max_items: int = Field(ge=1)
    max_bytes: int = Field(ge=1)


class JobsCfg(_M):
    """Asynchronous provider job polling caps."""

    poll_interval_s: float
    poll_jitter_s: float
    max_polls: int
    total_timeout_s: float


class LedgerCfg(_M):
    """Lease and in-flight wait settings for the paid-call ledger."""

    lease_ttl_s: int
    # The slice runner renews the run lease every ``lease_heartbeat_s``; a lease its owner has not
    # renewed for ``lease_stale_after_s`` may be taken by another worker before ``lease_ttl_s``
    # runs out, so the Cloud Tasks retries of a slice whose worker was killed (about +10, +30,
    # +70 and +150 s) resume the run instead of meeting a live lease and being dropped
    # (orchestration review 2026-10-30, F5). Keep the stale time above four heartbeats and below
    # the last retry.
    lease_heartbeat_s: float = Field(default=20.0, gt=0)
    lease_stale_after_s: float = Field(default=90.0, gt=0)
    in_flight_wait_s: float
    in_flight_poll_s: float
    # a 'started' attempt by this very worker and lease epoch older than this cannot still be
    # running (every provider HTTP exchange has a total timeout far below it): it is stale, as
    # after a worker restart that kept its lease, and is re-issued once as a possible duplicate
    in_flight_stale_s: float = Field(default=900.0, gt=0)


class Defaults(_M):
    """Defaults shared by every provider (budget, caps, retry, pacing, ledger)."""

    run_budget_usd: float
    tool_call_cap: int
    retry: RetryCfg
    ratelimit: RateLimitDefaults
    breaker: BreakerCfg
    pagination: PaginationCfg
    provider_jobs: JobsCfg
    ledger: LedgerCfg
    unknown_cost_reserve_usd: float = Field(gt=0)


class AuthCfg(_M):
    """How a provider authenticates (header and secret names; values from the environment)."""

    header: str
    scheme: Literal["raw", "bearer"]
    env: str
    extra_header: str | None = None
    extra_env: str | None = None
    source: str


class HttpCfg(_M):
    """Sourced HTTP timeouts and limits for one provider."""

    connect_s: float
    read_s: float
    write_s: float
    pool_s: float
    total_s: float
    max_bytes: int


class RateCfg(_M):
    """Sourced or placeholder rate limit for one provider, with its source."""

    rate_per_s: float
    burst: float
    source: str


CostModel = Literal[
    "usd_per_call_by_mode",
    "usd_per_call",
    "credits_per_document",
    "requests_per_page",
    "provider_reported_credits",
    "unknown",
]


class OpCost(_M):
    """Cost model of one operation, with its source (unknown is null, never 0)."""

    cost_model: CostModel
    source: str
    usd: float | None = None
    usd_by_mode: dict[str, float] | None = None
    usd_per_additional_result: float | None = None
    included_results: int | None = None
    credits: float | None = None
    unit: str = "credit"
    estimate_credits: float | None = None
    prd_hint: str | None = None

    @model_validator(mode="after")
    def _known_or_null(self) -> OpCost:
        if self.cost_model == "unknown" and (self.usd is not None or self.credits is not None):
            raise ValueError("an unknown cost model must leave usd and credits null")
        if self.cost_model == "unknown" and self.source != "unknown":
            raise ValueError("an unknown cost must say source: unknown")
        if self.usd is not None and self.usd <= 0:
            raise ValueError("a documented cost must be positive; unknown is null, never 0")
        return self


class ProviderCfg(_M):
    """One provider's configuration (display name internal only)."""

    display_name: str  # internal only
    base_url: str
    auth: AuthCfg
    http: HttpCfg
    rate_limit: RateCfg
    usd_per_credit: float | None = None
    unknown_cost_reserve_usd: float | None = None
    bill_on_failure: bool = False
    operations: dict[Operation, OpCost]
    page_limit: int | None = None
    org_attributes: tuple[str, ...] = ()
    org_free_attributes: tuple[str, ...] = ()
    people_attributes: tuple[str, ...] = ()
    people_free_attributes: tuple[str, ...] = ()
    people_page_limit: int | None = None
    page_url_field: str | None = None


class RouteTarget(_M):
    """A route's provider or built-in target."""

    provider: ProviderCode | None = None
    operation: Operation | None = None
    builtin: str | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> RouteTarget:
        if (self.provider is None) == (self.builtin is None):
            raise ValueError("a route target names a provider or a builtin, not both")
        return self


class RouteCfg(_M):
    """One routed need with its first choice and fallback."""

    need: str
    first: RouteTarget
    fallback: RouteTarget | None
    cost: str


class ProvidersConfig(_M):
    """The validated config/providers.yaml."""

    version: str
    defaults: Defaults
    providers: dict[ProviderCode, ProviderCfg]
    routes: tuple[RouteCfg, ...]

    def route(self, need: str) -> RouteCfg:
        """The route for a need (KeyError when unknown)."""
        for route in self.routes:
            if route.need == need:
                return route
        raise KeyError(need)

    def provider(self, code: ProviderCode) -> ProviderCfg:
        """One provider's configuration."""
        return self.providers[code]

    def op_cost(self, code: ProviderCode, op: Operation) -> OpCost:
        """The cost model of one operation."""
        return self.providers[code].operations[op]


def load_providers_config(path: Path | None = None) -> ProvidersConfig:
    """Load and validate config/providers.yaml (fail fast)."""
    raw: Any = yaml.safe_load((path or CONFIG_DIR / "providers.yaml").read_text(encoding="utf-8"))
    return ProvidersConfig.model_validate(raw)


# What the research caps switch lifts in the provider layer (owner decision 2026-10-24: with the
# caps off nothing the platform controls may stop a research, timeouts included). Hard platform
# limits stay where they are: a Cloud Run request ends at 3600 s and a Cloud Tasks dispatch at
# 1800 s, which is why a run is sliced and resumes from its checkpoint.
UNCAPPED_READ_S = 900.0
UNCAPPED_TOTAL_S = 1200.0
UNCAPPED_WAIT_S = 1800.0
UNCAPPED_POLLS = 10_000
UNCAPPED_JOB_S = 7200.0
UNCAPPED_RETRY_ATTEMPTS = 10
UNCAPPED_MAX_BYTES = 200_000_000


def uncapped_config(config: ProvidersConfig) -> ProvidersConfig:
    """The config with every timeout, wait, poll window and retry count raised to the values
    above (never lowered): a slow provider is waited for, a rate limit is paced through, a
    long asynchronous job is polled to its end, and a flaky one is retried up to ten times.
    Pagination caps are lifted by the runtime's page limit override (``app.container``)."""
    d = config.defaults
    defaults = d.model_copy(
        update={
            "retry": d.retry.model_copy(
                update={
                    "max_attempts": max(d.retry.max_attempts, UNCAPPED_RETRY_ATTEMPTS),
                    "max_interval_s": max(d.retry.max_interval_s, 120.0),
                }
            ),
            "ratelimit": d.ratelimit.model_copy(
                update={
                    "max_wait_s": max(d.ratelimit.max_wait_s, UNCAPPED_WAIT_S),
                    "max_cooldown_s": max(d.ratelimit.max_cooldown_s, UNCAPPED_WAIT_S),
                }
            ),
            "provider_jobs": d.provider_jobs.model_copy(
                update={
                    "max_polls": max(d.provider_jobs.max_polls, UNCAPPED_POLLS),
                    "total_timeout_s": max(d.provider_jobs.total_timeout_s, UNCAPPED_JOB_S),
                }
            ),
            "ledger": d.ledger.model_copy(
                update={
                    "in_flight_wait_s": max(d.ledger.in_flight_wait_s, UNCAPPED_TOTAL_S),
                    "in_flight_stale_s": max(d.ledger.in_flight_stale_s, UNCAPPED_JOB_S),
                }
            ),
        }
    )
    providers = {
        code: cfg.model_copy(
            update={
                "http": cfg.http.model_copy(
                    update={
                        "read_s": max(cfg.http.read_s, UNCAPPED_READ_S),
                        "write_s": max(cfg.http.write_s, 120.0),
                        "total_s": max(cfg.http.total_s, UNCAPPED_TOTAL_S),
                        "max_bytes": max(cfg.http.max_bytes, UNCAPPED_MAX_BYTES),
                    }
                )
            }
        )
        for code, cfg in config.providers.items()
    }
    return config.model_copy(update={"defaults": defaults, "providers": providers})
