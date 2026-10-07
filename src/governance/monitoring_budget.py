"""The monitoring budget (CG1, CG2, D-79): a hard, atomic monthly allowance per tenant, in plan
units (tenants see percent only).

* ``reserve``: one conditional update ``used + n <= allowance``: parallel probes can never pass
  it; an unpriced call reserves the configured upper bound; an exhausted allowance raises the
  typed ``BudgetExhausted`` (never a silent skip).
* The 80% crossing writes ``throttle_events`` (unique per tenant, period and step) and one
  tenant notification through ``spend.notify`` (dedupe key per period): exactly once.
* Ladder steps are recorded once each as they are reached; the ladder itself is
  ``governance.throttle.plan`` (pure).
* A new month is a new usage row: reset without rollover.

The one writer of ``monitoring_usage``, ``throttle_events`` and ``monitoring_budget_versions``.

GUARANTEE(test_the_monitoring_budget_is_atomic_and_alerts_once)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR
from spend import notify


class BudgetExhausted(RuntimeError):
    """The month's monitoring allowance is used up (typed state for the caller)."""


@dataclass(frozen=True)
class Reserved:
    """A reservation and the share used after it."""

    units: int
    used: int
    allowance: int
    used_percent: int
    crossed_80: bool


@lru_cache
def config() -> dict[str, Any]:
    """``config/budgets.yaml``."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "budgets.yaml").read_text())
    return data


def period_of(at: datetime) -> str:
    """The budget month (UTC)."""
    return f"{at.year}-{at.month:02d}"


async def allowance(s: Any, plan: str) -> int:
    """The newest operator-set allowance, else the plan default."""
    row = (
        await s.execute(
            text(
                "SELECT allowance_units FROM monitoring_budget_versions ORDER BY version DESC"
                " LIMIT 1"
            )
        )
    ).first()
    if row is not None:
        return int(row.allowance_units)
    return int(config()["plans"][plan])


async def set_allowance(s: Any, *, units: int, set_by: str, reason_code: str, now: datetime) -> int:
    """A new allowance version (operator plane)."""
    v = int(
        (
            await s.execute(
                text("SELECT coalesce(max(version), 0) + 1 FROM monitoring_budget_versions")
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO monitoring_budget_versions (version, allowance_units, set_by, reason_code,"
            " created_at) VALUES (:v, :u, :by, :r, :now)"
        ),
        {"v": v, "u": units, "by": set_by, "r": reason_code, "now": now},
    )
    return v


async def used(s: Any, at: datetime) -> int:
    """Units used this month."""
    row = (
        await s.execute(
            text("SELECT used FROM monitoring_usage WHERE period = :p"), {"p": period_of(at)}
        )
    ).first()
    return 0 if row is None else int(row.used)


async def reserve(s: Any, *, units: int | None, plan: str, now: datetime) -> Reserved:
    """Reserve ``units`` (None: unknown cost, the upper bound)."""
    n = int(config()["unknown_cost_units"]) if units is None else int(units)
    if n < 0:
        raise ValueError("units must not be negative")
    cap = await allowance(s, plan)
    period = period_of(now)
    await s.execute(
        text(
            "INSERT INTO monitoring_usage (period, used, updated_at) VALUES (:p, 0, :now)"
            " ON CONFLICT (tenant_id, period) DO NOTHING"
        ),
        {"p": period, "now": now},
    )
    row = (
        await s.execute(
            text(
                "UPDATE monitoring_usage SET used = used + :n, updated_at = :now WHERE period = :p"
                " AND used + :n <= :cap RETURNING used"
            ),
            {"n": n, "now": now, "p": period, "cap": cap},
        )
    ).first()
    if row is None:
        await _step(s, period, "hard_stop", 100, now)
        raise BudgetExhausted(period)
    after = int(row.used)
    pct = min(100, (after * 100) // cap) if cap else 100
    crossed = False
    if pct >= int(config()["alert_at_percent"]):
        crossed = await _step(s, period, "alert_80", pct, now)
        if crossed:
            await notify.enqueue(
                s,
                None,
                f"monitoring_budget_80:{period}",
                "tenant",
                "monitoring_budget_near_limit",
                {"percent": str(pct)},
                now,
            )
    for rung in config()["ladder"]:
        if pct >= int(rung["from_percent"]):
            await _step(s, period, str(rung["step"]), pct, now)
    return Reserved(n, after, cap, pct, crossed)


async def _step(s: Any, period: str, step: str, pct: int, now: datetime) -> bool:
    row = (
        await s.execute(
            text(
                "INSERT INTO throttle_events (period, step, used_percent, at) VALUES (:p, :s, :u,"
                " :now)"
                " ON CONFLICT (tenant_id, period, step) DO NOTHING RETURNING step"
            ),
            {"p": period, "s": step, "u": pct, "now": now},
        )
    ).first()
    return row is not None


async def tenant_view(s: Any, plan: str, now: datetime) -> dict[str, Any]:
    """What a tenant may see: percent only, never units, credits or dollars."""
    cap = await allowance(s, plan)
    u = await used(s, now)
    return {"period": period_of(now), "percent": min(100, (u * 100) // cap) if cap else 100}
