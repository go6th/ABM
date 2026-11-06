"""Metrics of the sign-in rate limits (an ``obs.py`` edge module: the only place in the package
that acquires a meter). The metric is in ``config/telemetry/metrics.yaml``.

* ``auth_rate_limit_degraded_total``: sign-in attempts let through without the shared Redis
  bucket (fail-open, ADR-0125). Any increase means the cross-instance sign-in limit is off; the
  observability module pages on the matching ``rate_limit_degraded`` log event.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("identity")
_degraded = _meter.create_counter(
    "auth_rate_limit_degraded_total",
    description="Sign-in attempts allowed without the shared Redis bucket",
)


def record_rate_limit_degraded(action: str) -> None:
    """One sign-in attempt for ``action`` let through with Redis unreachable."""
    del action  # the action is in the log event; the metric has no labels (closed set: none)
    _degraded.add(1)
