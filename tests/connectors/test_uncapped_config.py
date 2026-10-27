"""With the research caps off nothing the platform controls may stop a research (owner decision
2026-10-24): the provider timeouts, waits, poll windows and retry counts are raised, never
lowered, and the hard platform limits are left to the slice loop."""

from __future__ import annotations

from account_intelligence.connectors.settings import (
    UNCAPPED_JOB_S,
    UNCAPPED_MAX_BYTES,
    UNCAPPED_POLLS,
    UNCAPPED_READ_S,
    UNCAPPED_RETRY_ATTEMPTS,
    UNCAPPED_TOTAL_S,
    UNCAPPED_WAIT_S,
    load_providers_config,
    uncapped_config,
)


def test_every_timeout_wait_poll_and_retry_is_raised_and_none_lowered() -> None:
    base = load_providers_config()
    lifted = uncapped_config(base)
    d, u = base.defaults, lifted.defaults
    assert u.retry.max_attempts == max(d.retry.max_attempts, UNCAPPED_RETRY_ATTEMPTS) <= 10
    assert u.ratelimit.max_wait_s >= max(d.ratelimit.max_wait_s, UNCAPPED_WAIT_S)
    assert u.provider_jobs.max_polls >= UNCAPPED_POLLS
    assert u.provider_jobs.total_timeout_s >= UNCAPPED_JOB_S
    assert u.ledger.in_flight_stale_s >= u.provider_jobs.total_timeout_s
    for code, cfg in base.providers.items():
        http = lifted.providers[code].http
        assert http.read_s >= max(cfg.http.read_s, UNCAPPED_READ_S)
        assert http.total_s >= max(cfg.http.total_s, UNCAPPED_TOTAL_S)
        assert http.total_s > http.read_s
        assert http.max_bytes >= max(cfg.http.max_bytes, UNCAPPED_MAX_BYTES)
    # what is not a cap is untouched: routes, auth, costs, pacing rates
    assert lifted.routes == base.routes
    assert all(lifted.providers[c].auth == base.providers[c].auth for c in base.providers)
    assert all(
        lifted.providers[c].rate_limit == base.providers[c].rate_limit for c in base.providers
    )


def test_the_uncapped_config_is_idempotent() -> None:
    once = uncapped_config(load_providers_config())
    assert uncapped_config(once) == once
