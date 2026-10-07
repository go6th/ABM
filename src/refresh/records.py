"""Refresh records: the run with its decision, the per-row plan and the delta. Insert-only. The
one writer of ``refresh_runs``, ``refresh_row_plans``, ``deltas`` and ``delta_items``.

GUARANTEE(test_a_refresh_records_its_decision_plan_and_delta)
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from refresh.delta import Delta
from refresh.planner import RowPlan


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


async def record_run(
    s: Any,
    *,
    slot_id: UUID | None,
    account_id: UUID,
    kind: str,
    parent_run_id: UUID | None,
    decision: dict[str, Any],
    now: datetime,
) -> UUID:
    """The refresh run row."""
    rid: UUID = (
        await s.execute(
            text(
                "INSERT INTO refresh_runs (slot_id, account_id, kind, parent_run_id, decision,"
                " created_at)"
                " VALUES (:s, :a, :k, :p, CAST(:d AS jsonb), :now) RETURNING id"
            ),
            {
                "s": slot_id,
                "a": account_id,
                "k": kind,
                "p": parent_run_id,
                "d": json.dumps({k: _jsonable(v) for k, v in decision.items()}, sort_keys=True),
                "now": now,
            },
        )
    ).scalar_one()
    return rid


async def record_plan(s: Any, run_id: UUID, plan: Sequence[RowPlan]) -> None:
    """The per-row plan."""
    for p in plan:
        await s.execute(
            text(
                "INSERT INTO refresh_row_plans (refresh_run_id, row_key, action, reason,"
                " carried_from_run) VALUES (:r, :k, :a, :why, :c)"
            ),
            {"r": run_id, "k": p.row_key, "a": p.action, "why": p.reason, "c": p.carried_from_run},
        )


async def record_delta(
    s: Any, *, run_id: UUID, account_id: UUID, delta: Delta, score: int
) -> tuple[UUID, list[UUID]]:
    """The delta and its items (in order); returns the delta id and item ids."""
    did: UUID = (
        await s.execute(
            text(
                "INSERT INTO deltas (account_id, refresh_run_id, no_change, checked, checked_at,"
                " score)"
                " VALUES (:a, :r, :n, :c, :at, :sc) RETURNING id"
            ),
            {
                "a": account_id,
                "r": run_id,
                "n": delta.no_change,
                "c": list(delta.checked),
                "at": delta.checked_at,
                "sc": score,
            },
        )
    ).scalar_one()
    ids: list[UUID] = []
    for pos, it in enumerate(delta.items):
        d = asdict(it)
        ids.append(
            (
                await s.execute(
                    text(
                        "INSERT INTO delta_items (delta_id, position, kind, signal_type,"
                        " identity, fact_id,"
                        " before_value, after_value, direction, base, source_code) VALUES (:d,"
                        " :p, :k, :t,"
                        " :i, :f, :b, :af, :dir, :base, :sc) RETURNING id"
                    ),
                    {
                        "d": did,
                        "p": pos,
                        "k": d["kind"],
                        "t": d["signal_type"],
                        "i": d["identity"],
                        "f": d["fact_id"],
                        "b": d["before"],
                        "af": d["after"],
                        "dir": d["direction"],
                        "base": d["base"],
                        "sc": d["source_code"],
                    },
                )
            ).scalar_one()
        )
    return did, ids
