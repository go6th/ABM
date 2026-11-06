"""Sign-in survives a Redis outage (fix prompt D, task 4c; ADR-0125).

The sign-in buckets (``login``, ``sso_discover``) fail OPEN when the shared Redis cannot be
reached: the attempt is allowed through this instance's own bounded bucket (so one instance still
refuses a burst), ``rate_limit_degraded`` is logged and ``auth_rate_limit_degraded_total`` counts
it (the observability module pages on the log event). Every other bucket stays fail CLOSED (503).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from account_intelligence.api.errors import APIProblem
from account_intelligence.api.problem import DependencyUnavailable
from identity import obs as identity_obs
from identity import rate_limit


class _DownStore:
    async def take(self, *a: Any, **k: Any) -> float:
        raise ConnectionError("redis is down")


@pytest.fixture
def redis_down(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    counted: list[str] = []
    monkeypatch.setattr(
        rate_limit, "get_settings", lambda: SimpleNamespace(redis_url="rediss://10.0.0.3:6378/0")
    )
    monkeypatch.setattr(rate_limit, "_redis_store", lambda: _DownStore())
    monkeypatch.setattr(identity_obs, "record_rate_limit_degraded", counted.append)
    rate_limit._memory.clear()
    return counted


def _request() -> Any:
    return SimpleNamespace(client=SimpleNamespace(host="198.51.100.7"))


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["login", "sso_discover"])
async def test_sign_in_fails_open_with_an_alert_when_redis_is_down(
    redis_down: list[str], caplog: pytest.LogCaptureFixture, action: str
) -> None:
    with caplog.at_level(logging.WARNING):
        await rate_limit.consume(_request(), account="lee@example.com", action=action)
    assert redis_down == [action]
    events = [r for r in caplog.records if r.getMessage() == "rate_limit_degraded"]
    assert events and events[0].levelno == logging.WARNING
    assert getattr(events[0], "kind", None) == action


@pytest.mark.asyncio
async def test_the_fail_open_bucket_is_still_bounded_per_instance(redis_down: list[str]) -> None:
    capacity = int(rate_limit._limits()["account"][0])
    for _ in range(capacity):
        await rate_limit.consume(_request(), account="burst@example.com", action="login")
    with pytest.raises(APIProblem) as refused:
        await rate_limit.consume(_request(), account="burst@example.com", action="login")
    assert refused.value.status == 429


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    ["enroll", "resend", "verify", "password_reset", "password_reset_verify", "invite_accept"],
)
async def test_every_other_auth_bucket_stays_fail_closed(
    redis_down: list[str], action: str
) -> None:
    with pytest.raises(DependencyUnavailable):
        await rate_limit.consume(_request(), account="lee@example.com", action=action)
    assert redis_down == []


@pytest.mark.asyncio
async def test_the_per_user_buckets_stay_fail_closed(redis_down: list[str]) -> None:
    with pytest.raises(DependencyUnavailable):
        await rate_limit.consume_user("tenant", "user", "export")
    assert redis_down == []


def test_the_degraded_metric_and_its_alert_are_declared() -> None:
    import yaml

    from account_intelligence.config import CONFIG_DIR

    metrics = yaml.safe_load((CONFIG_DIR / "telemetry/metrics.yaml").read_text())["metrics"]
    assert metrics["auth_rate_limit_degraded_total"]["type"] == "counter"
    from pathlib import Path

    tf = (
        Path(__file__).resolve().parents[2] / "infra/modules/observability/platform.tf"
    ).read_text()
    import re

    assert 'event = "rate_limit_degraded"' in tf
    policies = tf.split('resource "google_monitoring_alert_policy" "event"')[1]
    assert re.search(r'rate_limit_degraded\s*=\s*\{\s*severity\s*=\s*"page"', policies)
