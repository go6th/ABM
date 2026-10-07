"""The refresh plan (RF1, C27): which tracker rows a refresh re-runs and which it carries forward.

* A **probe** re-runs only the rows of the probe sources (job postings, news) that are due.
* A **full refresh** re-runs every High priority row (PRD 4.5) and every row past its shelf life;
  the rest are carried forward, visibly "not due" with the run they were last checked in.
* A row whose signal has no cadence is never due by shelf life (D-71); a High row still re-runs in
  a full refresh (PRD), recorded as reason ``high_priority``.

Pure. GUARANTEE(test_the_plan_reruns_only_due_rows)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from refresh.policy import Policy, effective_days, is_due

Kind = Literal["probe", "refresh"]
PROBE_ROWS = {
    "job_postings": ("job_board_by_lane",),
    "news_events": ("company_news", "leadership_changes"),
}


@dataclass(frozen=True)
class PriorRow:
    """A row of the latest tracker version."""

    row_key: str
    priority: str
    last_checked_at: datetime | None
    run_id: str


@dataclass(frozen=True)
class RowPlan:
    """The plan for one row."""

    row_key: str
    action: Literal["rerun", "not_due"]
    reason: Literal["high_priority", "shelf_life", "probe_source", "not_due", "no_cadence"]
    carried_from_run: str | None


def plan(
    kind: Kind,
    rows: Sequence[PriorRow],
    policy: Policy,
    now: datetime,
    *,
    probe_sources: Sequence[str],
    overrides: Mapping[str, int] | None = None,
) -> list[RowPlan]:
    """The per-row plan (in row order)."""
    probe_keys = {k for src in probe_sources for k in PROBE_ROWS.get(src, ())}
    out: list[RowPlan] = []
    for r in sorted(rows, key=lambda x: x.row_key):
        entry = policy.entries.get(r.row_key)
        days = effective_days(entry, policy, (overrides or {}).get(r.row_key)) if entry else None
        due = is_due(now, r.last_checked_at, days)
        if kind == "probe":
            if r.row_key in probe_keys and due:
                out.append(RowPlan(r.row_key, "rerun", "probe_source", None))
            else:
                out.append(
                    RowPlan(
                        r.row_key, "not_due", "no_cadence" if days is None else "not_due", r.run_id
                    )
                )
        elif r.priority == "high":
            out.append(RowPlan(r.row_key, "rerun", "shelf_life" if due else "high_priority", None))
        elif due:
            out.append(RowPlan(r.row_key, "rerun", "shelf_life", None))
        else:
            out.append(
                RowPlan(r.row_key, "not_due", "no_cadence" if days is None else "not_due", r.run_id)
            )
    return out
