"""Plan limits (MT2): checked atomically where usage is created.

``consume`` increments the tenant's counter for one dimension with ONE conditional update
(``used + n <= limit``) after ensuring the row exists; concurrent requests serialise on the row
and can never pass the limit. A refusal is ``LimitReached`` naming the dimension and the limit in
units (the API answers 402). Crossing the soft threshold (percent of the limit, proposal 80)
queues one tenant notice per period and dimension. Plans and assignments are insert-only versions
(``plan_limits`` platform versions, ``plan_assignments`` per tenant); a tenant without an
assignment is on the default plan. Counted dimensions: accounts and seats over all time
("all"), runs and refreshes per period, connectors over all time.

GUARANTEE(test_limits_are_atomic_under_concurrency)
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from metering.events import period_of

PERIODIC = frozenset({"runs_per_period", "refreshes_per_period"})
DIMENSIONS = (
    "accounts",
    "runs_per_period",
    "refreshes_per_period",
    "seats",
    "connectors",
    "storage_mb",
)
# the plan limits on research that RESEARCH_CAPS=off lifts (dev_limits)
RESEARCH_DIMENSIONS = frozenset({"accounts", "runs_per_period", "refreshes_per_period"})
UNITS = {
    "accounts": "accounts",
    "runs_per_period": "runs",
    "refreshes_per_period": "refreshes",
    "seats": "seats",
    "connectors": "connectors",
    "storage_mb": "MB",
}


class LimitReached(PermissionError):
    """A plan limit refused the action (identifiers and units only)."""

    def __init__(self, dimension: str, limit: int) -> None:
        super().__init__(f"{dimension}: {limit} {UNITS.get(dimension, '')}".strip())
        self.dimension = dimension
        self.limit = limit


@lru_cache
def config() -> dict[str, Any]:
    """``config/plans.yaml`` (read once)."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "plans.yaml").read_text())
    return data


def period_key(dimension: str, at: datetime) -> str:
    """The counter period of a dimension: the month for per-period ones, ``all`` otherwise."""
    return period_of(at) if dimension in PERIODIC else "all"


async def plan_code(s: Any) -> str:
    """The context tenant's plan (latest assignment, else the default plan)."""
    found: str | None = (
        await s.execute(
            text("SELECT plan_code FROM plan_assignments ORDER BY version DESC LIMIT 1")
        )
    ).scalar_one_or_none()
    return found or str(config()["default_plan"])


async def limits(s: Any) -> dict[str, int]:
    """The context tenant's limits: the latest version of its plan, then any quota override
    (``quota_versions``, the operator plane's insert-only per-tenant versions)."""
    code = await plan_code(s)
    rows = (
        await s.execute(
            text(
                "SELECT dimension, limit_value FROM plan_limits WHERE plan_code = :p"
                " AND version = (SELECT max(version) FROM plan_limits WHERE plan_code = :p)"
            ),
            {"p": code},
        )
    ).all()
    out = {r.dimension: int(r.limit_value) for r in rows}
    overrides = await _overrides(s)
    out.update(overrides)
    return out


async def _overrides(s: Any) -> dict[str, int]:
    rows = (
        await s.execute(
            text(
                "SELECT DISTINCT ON (dimension) dimension, limit_value FROM quota_versions"
                " ORDER BY dimension, version DESC"
            )
        )
    ).all()
    return {r.dimension: int(r.limit_value) for r in rows}


async def consume(s: Any, dimension: str, at: datetime, n: int = 1) -> int:
    """Atomically take ``n`` units of ``dimension``; returns the new count; LimitReached when
    the plan does not allow it. Runs in the caller's transaction (with the action)."""
    if dimension not in DIMENSIONS or n < 1:
        raise ValueError("unknown dimension or bad amount")
    limit = (await limits(s)).get(dimension)
    from account_intelligence.dev_limits import UNCAPPED, research_uncapped

    if research_uncapped() and dimension in RESEARCH_DIMENSIONS:
        limit = UNCAPPED  # research caps off (dev_limits): still counted, never refused
    if limit is None:
        raise LimitReached(dimension, 0)  # a dimension without a limit is not allowed (closed)
    period = period_key(dimension, at)
    await s.execute(
        text(
            "INSERT INTO usage_counters (period, dimension, used, updated_at)"
            " VALUES (:p, :d, 0, :at) ON CONFLICT (tenant_id, period, dimension) DO NOTHING"
        ),
        {"p": period, "d": dimension, "at": at},
    )
    row = (
        await s.execute(
            text(
                "UPDATE usage_counters SET used = used + :n, updated_at = :at"
                " WHERE period = :p AND dimension = :d AND used + :n <= :lim RETURNING used"
            ),
            {"n": n, "at": at, "p": period, "d": dimension, "lim": limit},
        )
    ).first()
    if row is None:
        raise LimitReached(dimension, limit)
    used = int(row.used)
    await _soft_threshold(s, dimension, period, used, limit, at)
    return used


async def _soft_threshold(
    s: Any, dimension: str, period: str, used: int, limit: int, at: datetime
) -> None:
    percent = int(config()["soft_threshold_percent"])
    if limit <= 0 or used * 100 < limit * percent:
        return
    first = (
        await s.execute(
            text(
                "INSERT INTO usage_thresholds (period, dimension, percent, at)"
                " VALUES (:p, :d, :pc, :at) ON CONFLICT DO NOTHING RETURNING period"
            ),
            {"p": period, "d": dimension, "pc": percent, "at": at},
        )
    ).first()
    if first is None:
        return  # already noticed this period
    from spend import notify

    await notify.enqueue(
        s,
        None,
        f"usage:{period}:{dimension}",
        "tenant",
        "plan_usage_near_limit",
        {"dimension": dimension, "percent": str(percent)},
        at,
    )


async def counters(s: Any, at: datetime) -> dict[str, int]:
    """The context tenant's current counts per dimension (this period for periodic ones)."""
    out: dict[str, int] = {}
    for d in DIMENSIONS:
        used = (
            await s.execute(
                text("SELECT used FROM usage_counters WHERE period = :p AND dimension = :d"),
                {"p": period_key(d, at), "d": d},
            )
        ).scalar_one_or_none()
        out[d] = int(used or 0)
    return out


async def release(s: Any, dimension: str, at: datetime, n: int = 1) -> None:
    """Give back ``n`` units of an all-time dimension (a seat or a connector that ended); never
    below zero (one conditional update)."""
    if dimension in PERIODIC:
        raise ValueError("periodic usage is never given back")
    await s.execute(
        text(
            "UPDATE usage_counters SET used = used - :n, updated_at = :at"
            " WHERE period = 'all' AND dimension = :d AND used >= :n"
        ),
        {"n": n, "at": at, "d": dimension},
    )


def limit_problem(e: LimitReached) -> Any:
    """The API problem for a refused limit: 402 naming the dimension and the limit in units."""
    from account_intelligence.api.errors import APIProblem

    return APIProblem(
        402,
        "plan_limit_reached",
        "Plan limit reached",
        f"Your plan allows {e.limit} {UNITS.get(e.dimension, e.dimension)}"
        f" ({e.dimension.replace('_', ' ')}).",
    )
