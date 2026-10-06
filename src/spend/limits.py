"""Soft and hard limits and the funding mode (M1, M3; D-45, D-46).

**Pure rules** (integer micro-dollars, no clock, no I/O):

* ``soft_threshold(cap, pct)`` is ``cap * pct // 100``; with ``pct < 100`` (validated at startup)
  the soft limit is always strictly below the hard limit, and **hard is the approved cap**: the
  connector's atomic reserve refuses anything beyond it, this module never raises it;
* ``crossed(inputs)``: committed plus held has reached the soft limit of the current cap version;
* ``mode(inputs)``: ``stopped`` once the current version's pause was declined or timed out,
  ``paused`` while its pause request is open, ``priority_only`` at or past the soft limit (High
  rows and units already started only, D-46), else ``normal``. A new cap version re-arms it.

**In the claim transaction** (``after_spend_change``): the connector's ledger calls it after every
reserve and true-up, inside the same transaction as the counter update, so the soft crossing is
recorded exactly once per cap version (the partial unique index refuses a second row; a duplicate
delivery or a race inserts nothing) together with its outbox notification (M10).

GUARANTEE(test_soft_is_always_below_hard_and_integer): property test.
GUARANTEE(test_soft_crossing_fires_once_per_cap_version): M1 on Postgres.
GUARANTEE(test_funding_mode_follows_the_pause_lifecycle): M3 mode table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.config import V2Config, load_v2_config
from spend.money import usd_to_micros


class Mode(StrEnum):
    """Funding mode (the unit scheduler's ``FundingMode`` has the same values)."""

    NORMAL = "normal"
    PRIORITY_ONLY = "priority_only"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True)
class LimitInputs:
    """What one consistent read of a run gives the limit rules."""

    cap_micros: int | None
    cap_version: int | None
    committed_micros: int
    held_micros: int
    pause_open: bool = False
    stopped: bool = False


def soft_threshold(cap_micros: int, pct: int) -> int:
    """The soft limit in micro-dollars (always below the cap for ``1 <= pct < 100``)."""
    if not 1 <= pct < 100:
        raise ValueError("the soft limit percentage must be between 1 and 99")
    if cap_micros < 0:
        raise ValueError("a cap cannot be negative")
    return cap_micros * pct // 100


def crossed(inputs: LimitInputs, pct: int) -> bool:
    """Committed plus held has reached the soft limit of the current cap."""
    if inputs.cap_micros is None:
        return False
    return inputs.committed_micros + inputs.held_micros >= soft_threshold(inputs.cap_micros, pct)


def mode(inputs: LimitInputs, pct: int) -> Mode:
    """The funding mode (see the module docstring)."""
    if inputs.stopped:
        return Mode.STOPPED
    if inputs.pause_open:
        return Mode.PAUSED
    if crossed(inputs, pct):
        return Mode.PRIORITY_ONLY
    return Mode.NORMAL


READ = text(
    """
    WITH open_attempts AS (
       SELECT e.reserved_usd
         FROM tool_call_events e
        WHERE e.run_id = :r AND e.event = 'started'
          AND NOT EXISTS (
             SELECT 1 FROM tool_call_events t
              WHERE t.run_id = e.run_id AND t.args_hash = e.args_hash AND t.attempt = e.attempt
                AND t.event IN ('done', 'failed', 'abandoned'))
    ), decided AS (
       SELECT request_id FROM spend_limit_events
        WHERE run_id = :r AND event IN ('approved', 'declined', 'timed_out')
    )
    SELECT r.spent_usd, r.budget_usd, r.cap_version,
           COALESCE((SELECT sum(reserved_usd) FROM open_attempts), 0) AS held_usd,
           EXISTS (SELECT 1 FROM spend_limit_events p
                    WHERE p.run_id = r.id AND p.cap_version = r.cap_version
                      AND p.event = 'pause_opened'
                      AND p.request_id NOT IN (SELECT request_id FROM decided)) AS pause_open,
           EXISTS (SELECT 1 FROM spend_limit_events d
                    WHERE d.run_id = r.id AND d.cap_version = r.cap_version
                      AND d.event IN ('declined', 'timed_out')) AS stopped
      FROM account_runs r WHERE r.id = :r
    """
)


async def read_inputs(s: AsyncSession, run_id: UUID) -> LimitInputs | None:
    """One statement, so one consistent snapshot of the counters and the pause state (M2)."""
    row = (await s.execute(READ, {"r": run_id})).first()
    if row is None:
        return None
    spent = usd_to_micros(row.spent_usd)
    held = usd_to_micros(row.held_usd)
    return LimitInputs(
        cap_micros=usd_to_micros(row.budget_usd) if row.budget_usd is not None else None,
        cap_version=row.cap_version,
        committed_micros=max(0, spent - held),
        held_micros=held,
        pause_open=bool(row.pause_open),
        stopped=bool(row.stopped),
    )


async def funding_mode(s: AsyncSession, run_id: UUID, cfg: V2Config | None = None) -> Mode:
    """The run's funding mode now (``stopped`` when the run cannot be read: fail closed)."""
    inputs = await read_inputs(s, run_id)
    if inputs is None:
        return Mode.STOPPED
    return mode(inputs, (cfg or load_v2_config()).soft_limit_pct)


async def after_spend_change(
    s: AsyncSession, run_id: UUID, now: datetime, cfg: V2Config | None = None
) -> bool:
    """Record the soft crossing of the current cap version once (and its notification), in the
    caller's transaction. Returns True when this call recorded it."""
    from spend import notify  # local: notify imports nothing from here, kept acyclic anyway

    cfg = cfg or load_v2_config()
    inputs = await read_inputs(s, run_id)
    if inputs is None or inputs.cap_version is None or not crossed(inputs, cfg.soft_limit_pct):
        return False
    inserted: Any = (
        await s.execute(
            text(
                "INSERT INTO spend_limit_events (run_id, cap_version, event, detail_json, at)"
                " VALUES (:r, :v, 'soft_crossed',"
                " jsonb_build_object('soft_pct', CAST(:pct AS integer)), :at)"
                " ON CONFLICT (run_id, cap_version) WHERE event = 'soft_crossed' DO NOTHING"
                " RETURNING id"
            ),
            {"r": run_id, "v": inputs.cap_version, "pct": cfg.soft_limit_pct, "at": now},
        )
    ).first()
    if inserted is None:
        return False
    await notify.enqueue(
        s,
        run_id,
        f"soft:{run_id}:{inputs.cap_version}",
        "operator",
        "spend_soft_limit",
        {"run_id": str(run_id), "cap_version": inputs.cap_version},
        now,
    )
    await notify.enqueue(
        s,
        run_id,
        f"soft-tenant:{run_id}:{inputs.cap_version}",
        "tenant",
        "run_spend_near_limit",
        {"run_id": str(run_id)},
        now,
    )
    return True
