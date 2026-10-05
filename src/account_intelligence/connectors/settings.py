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
    in_flight_wait_s: float
    in_flight_poll_s: float


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
