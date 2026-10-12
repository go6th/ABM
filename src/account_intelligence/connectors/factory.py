"""Build a ``ConnectorRuntime`` from config, secrets and injected infrastructure."""

from __future__ import annotations

import random
import re
import uuid
from collections.abc import Mapping

import httpx

from account_intelligence.connectors.base import (
    Clock,
    Connector,
    ProviderCode,
    Sleep,
    SystemClock,
    real_sleep,
)
from account_intelligence.connectors.breaker import (
    BreakerConfig,
    BreakerStore,
    CircuitBreaker,
    InMemoryBreakerStore,
)
from account_intelligence.connectors.budget import BudgetDefaults
from account_intelligence.connectors.cost import CostModel
from account_intelligence.connectors.http import HttpSettings, make_client
from account_intelligence.connectors.ledger import Ledger, TxnFactory
from account_intelligence.connectors.payloads import PayloadStore
from account_intelligence.connectors.providers.p_org import OrgConnector
from account_intelligence.connectors.providers.p_people import PeopleConnector
from account_intelligence.connectors.providers.p_scrape import ScrapeConnector
from account_intelligence.connectors.providers.p_search import SearchConnector
from account_intelligence.connectors.providers.p_signals import SignalsConnector
from account_intelligence.connectors.ratelimit import (
    BucketLimit,
    InMemoryRateStore,
    RateLimiter,
    RateStore,
)
from account_intelligence.connectors.security import Secret, UrlPolicy, load_secret
from account_intelligence.connectors.service import ConnectorRuntime
from account_intelligence.connectors.settings import ProviderCfg, ProvidersConfig


def build_connectors(config: ProvidersConfig) -> dict[ProviderCode, Connector]:
    """One connector per provider code from config."""
    org = config.provider(ProviderCode.P_ORG)
    signals = config.provider(ProviderCode.P_SIGNALS)
    return {
        ProviderCode.P_SEARCH: SearchConnector(),
        ProviderCode.P_SCRAPE: ScrapeConnector(),
        ProviderCode.P_SIGNALS: SignalsConnector(page_limit=signals.page_limit or 100),
        ProviderCode.P_PEOPLE: PeopleConnector(),
        ProviderCode.P_ORG: OrgConnector(
            org_attributes=org.org_attributes,
            people_attributes=org.people_attributes,
            people_page_limit=org.people_page_limit or 10,
            page_url_field=org.page_url_field or "",
        ),
    }


def http_settings(cfg: ProviderCfg) -> HttpSettings:
    """Timeouts, limits and size caps for one provider's HTTP client."""
    return HttpSettings(
        base_url=cfg.base_url,
        connect_s=cfg.http.connect_s,
        read_s=cfg.http.read_s,
        write_s=cfg.http.write_s,
        pool_s=cfg.http.pool_s,
        total_s=cfg.http.total_s,
        max_bytes=cfg.http.max_bytes,
    )


# Template and placeholder values are "not set": they must never reach a provider as a credential.
_PLACEHOLDER = re.compile(r"(?i)^(your[-_]|replace[-_]with|changeme|placeholder|<)|example")


def credential_names(cfg: ProviderCfg) -> tuple[str, ...]:
    """Every variable a provider needs (signals needs its key AND its token)."""
    extra = (cfg.auth.extra_env,) if cfg.auth.extra_header and cfg.auth.extra_env else ()
    return (cfg.auth.env, *extra)


def _usable(env: Mapping[str, str] | None, name: str) -> Secret | None:
    secret = load_secret(dict(env) if env is not None else None, name)
    if secret is None or _PLACEHOLDER.search(secret.reveal().strip()):
        return None
    return secret


def provider_configured(cfg: ProviderCfg, env: Mapping[str, str] | None) -> bool:
    """A provider counts as configured only when every credential it needs is set and none is a
    template placeholder. Readiness and the request path share this one rule."""
    return all(_usable(env, name) is not None for name in credential_names(cfg))


def auth_headers(cfg: ProviderCfg, env: Mapping[str, str] | None) -> dict[str, str]:
    """Authentication headers from the environment or mounted secrets (never logged). An
    unconfigured provider gets NO credential header at all (not a partial or placeholder one), so
    its calls fail as unauthenticated and the run marks the affected rows not checked."""
    headers = {"accept": "application/json", "user-agent": "account-intelligence-connectors/1"}
    if not provider_configured(cfg, env):
        return headers
    secret = _usable(env, cfg.auth.env)
    assert secret is not None
    headers[cfg.auth.header] = _render(cfg.auth.scheme, secret)
    if cfg.auth.extra_header and cfg.auth.extra_env:
        extra = _usable(env, cfg.auth.extra_env)
        assert extra is not None
        headers[cfg.auth.extra_header] = extra.reveal()
    return headers


def _render(scheme: str, secret: Secret) -> str:
    return f"Bearer {secret.reveal()}" if scheme == "bearer" else secret.reveal()


def build_runtime(
    config: ProvidersConfig,
    *,
    txn: TxnFactory,
    payloads: PayloadStore,
    rate_store: RateStore | None = None,
    breaker_store: BreakerStore | None = None,
    transports: Mapping[ProviderCode, httpx.AsyncBaseTransport] | None = None,
    env: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    sleep: Sleep | None = None,
    rng: random.Random | None = None,
    url_policy: UrlPolicy | None = None,
    worker_id: str | None = None,
) -> ConnectorRuntime:
    """Assemble the connector runtime: clients, limiter, breaker, ledger, cost model, policies."""
    clock = clock or SystemClock()
    sleep = sleep or real_sleep
    d = config.defaults
    settings = {code: http_settings(cfg) for code, cfg in config.providers.items()}
    clients = {
        code: make_client(
            settings[code],
            headers=auth_headers(cfg, env),
            transport=(transports or {}).get(code),
            env=env,
        )
        for code, cfg in config.providers.items()
    }
    limiter = RateLimiter(
        rate_store or InMemoryRateStore(),
        {
            c: BucketLimit(p.rate_limit.rate_per_s, p.rate_limit.burst)
            for c, p in config.providers.items()
        },
        clock,
        sleep,
        max_wait_s=d.ratelimit.max_wait_s,
        max_cooldown_s=d.ratelimit.max_cooldown_s,
        default_cooldown_s=d.ratelimit.default_cooldown_s,
    )
    breaker = CircuitBreaker(
        breaker_store or InMemoryBreakerStore(),
        clock,
        BreakerConfig(
            d.breaker.failure_threshold,
            d.breaker.window_s,
            d.breaker.cooldown_s,
            d.breaker.half_open_probes,
        ),
    )
    ledger = Ledger(
        txn,
        clock,
        lease_ttl_s=d.ledger.lease_ttl_s,
        budget_defaults=BudgetDefaults(d.run_budget_usd, d.tool_call_cap),
    )
    return ConnectorRuntime(
        config=config,
        connectors=build_connectors(config),
        clients=clients,
        http=settings,
        limiter=limiter,
        breaker=breaker,
        ledger=ledger,
        payloads=payloads,
        cost=CostModel(config),
        clock=clock,
        sleep=sleep,
        rng=rng or random.Random(),
        url_policy=url_policy or UrlPolicy(),
        worker_id=worker_id or f"worker-{uuid.uuid4()}",
    )
