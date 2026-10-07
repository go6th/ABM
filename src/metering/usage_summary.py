"""The billing-ready period summary (MT3): per kind, units with their base and as-of time,
recomputed from the usage events (never from the mutable counters), plus each limit as a share
of the plan (D-63: allowance as a percentage, never credits, dollars or provider names).

GUARANTEE(test_the_api_refuses_beyond_the_plan_in_units)
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from sqlalchemy import text

from account_intelligence.safety.client_output import assert_client_safe
from metering.events import period_of
from metering.limits import UNITS, counters, limits


async def summary(s: Any, at: datetime, period: str | None = None) -> dict[str, Any]:
    """The context tenant's usage for ``period`` (default: the current one)."""
    p = period or period_of(at)
    rows = (
        await s.execute(
            text(
                "SELECT kind, count(*) AS events, coalesce(sum(units), 0) AS units"
                " FROM usage_events WHERE period = :p GROUP BY kind ORDER BY kind"
            ),
            {"p": p},
        )
    ).all()
    lim = await limits(s)
    used = await counters(s, at)
    items = []
    for d, unit in UNITS.items():
        cap = lim.get(d)
        items.append(
            {
                "dimension": d,
                "used": used.get(d, 0),
                "limit": cap,
                "unit": unit,
                "percent": None if not cap else min(100, round(100 * used.get(d, 0) / cap)),
            }
        )
    return {
        "period": p,
        "as_of": at.isoformat(),
        "events": [
            {
                "kind": r.kind,
                "events": int(r.events),
                "units": int(r.units),
                "base": f"usage events of kind {r.kind} in {p}",
            }
            for r in rows
        ],
        "items": items,
    }


def summary_csv(body: dict[str, Any]) -> bytes:
    """The summary as CSV (scanned: units only)."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["period", "as_of", "kind", "events", "units", "base"])
    for e in body["events"]:
        w.writerow([body["period"], body["as_of"], e["kind"], e["events"], e["units"], e["base"]])
    text_ = out.getvalue()
    assert_client_safe(text_.splitlines())
    return text_.encode()
