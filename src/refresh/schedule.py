"""Periods, jitter and catch-up selection (RF8). Pure.

* ``period_key``: the slot period of a cadence at an instant (ISO week, month, quarter; UTC).
* ``jitter_s``: a deterministic offset inside the window from (account, kind, period), so slots
  spread across the hour and the same slot always gets the same offset.
* ``select_catch_up``: after an outage, the missed slots to run this tick: oldest due first,
  priority A before others on ties, at most ``per_tick`` in total and ``per_tenant`` per tenant
  (no stampede); the rest wait for later ticks.

GUARANTEE(test_slot_periods_jitter_and_catch_up_are_bounded)
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


def period_key(cadence: str, at: datetime) -> str:
    """``2026-W10``, ``2026-03`` or ``2026-Q1`` (UTC calendar of ``at``)."""
    if cadence == "weekly":
        year, week, _ = at.isocalendar()
        return f"{year}-W{week:02d}"
    if cadence == "monthly":
        return f"{at.year}-{at.month:02d}"
    if cadence == "quarterly":
        return f"{at.year}-Q{(at.month - 1) // 3 + 1}"
    raise ValueError(f"no period for cadence {cadence!r}")


def jitter_s(account_id: UUID, kind: str, period: str, window_s: int) -> int:
    """A stable offset in ``[0, window_s)``."""
    if window_s <= 0:
        return 0
    digest = hashlib.sha256(f"{account_id}:{kind}:{period}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % window_s


@dataclass(frozen=True)
class MissedSlot:
    """A slot that should have run."""

    tenant_id: UUID
    account_id: UUID
    kind: str
    period: str
    due_at: datetime
    priority_a: bool


def select_catch_up(
    missed: Sequence[MissedSlot], *, per_tick: int, per_tenant: int
) -> list[MissedSlot]:
    """The slots to run this tick (deterministic order)."""
    ordered = sorted(
        missed, key=lambda m: (m.due_at, not m.priority_a, str(m.account_id), m.kind, m.period)
    )
    taken: list[MissedSlot] = []
    by_tenant: Counter[UUID] = Counter()
    seen: set[tuple[UUID, str, str]] = set()
    for m in ordered:  # LOOP(catch-up selection): cap=len(missed); exit=per_tick reached or end
        if len(taken) >= per_tick:
            break
        key = (m.account_id, m.kind, m.period)
        if key in seen or by_tenant[m.tenant_id] >= per_tenant:
            continue
        seen.add(key)
        by_tenant[m.tenant_id] += 1
        taken.append(m)
    return taken
