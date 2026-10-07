"""Debounce persistence (RF5): each probe records one observation per watched item; the state
after it comes from ``refresh.debounce.observe`` applied to the item's latest row. The one writer
of ``missing_observations``.

GUARANTEE(test_removals_need_consecutive_absences_and_outages_do_not_count)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from refresh.debounce import Event, Observation, Tracked, observe


async def record(
    s: Any,
    *,
    account_id: UUID,
    item_key: str,
    probe_run_id: UUID,
    seen: Observation,
    absences_to_remove: int,
    now: datetime,
) -> Event:
    """Record one observation; returns the debounce event."""
    last = (
        await s.execute(
            text(
                "SELECT state_after, absences FROM missing_observations WHERE account_id = :a"
                " AND item_key = :k ORDER BY id DESC LIMIT 1"
            ),
            {"a": account_id, "k": item_key},
        )
    ).first()
    prev = Tracked(last.state_after, int(last.absences)) if last else Tracked("present", 0)
    nxt, event = observe(prev, seen, absences_to_remove=absences_to_remove)
    await s.execute(
        text(
            "INSERT INTO missing_observations (account_id, item_key, probe_run_id, observation,"
            " state_after, absences, at) VALUES (:a, :k, :r, :o, :st, :n, :now)"
            " ON CONFLICT (probe_run_id, item_key) DO NOTHING"
        ),
        {
            "a": account_id,
            "k": item_key,
            "r": probe_run_id,
            "o": seen,
            "st": nxt.state,
            "n": nxt.absences,
            "now": now,
        },
    )
    return event


async def insert(
    s: Any,
    *,
    account_id: UUID,
    item_key: str,
    probe_run_id: UUID,
    seen: Observation,
    state: Tracked,
    now: datetime,
) -> None:
    """Record an observation whose debounce state the caller already computed (the probe
    computes all of them before its run row exists)."""
    await s.execute(
        text(
            "INSERT INTO missing_observations (account_id, item_key, probe_run_id, observation,"
            " state_after, absences, at) VALUES (:a, :k, :r, :o, :st, :n, :now)"
            " ON CONFLICT (probe_run_id, item_key) DO NOTHING"
        ),
        {
            "a": account_id,
            "k": item_key,
            "r": probe_run_id,
            "o": seen,
            "st": state.state,
            "n": state.absences,
            "now": now,
        },
    )
