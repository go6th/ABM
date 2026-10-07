"""Refresh slots (RF8): exactly one probe or refresh per (account, kind, period).

* ``ensure``: create the slot if absent (``ON CONFLICT DO NOTHING``): a lost tick re-run, a
  duplicate delivery or an overlapping manual request finds the same row.
* ``claim``: take due pending slots of the context tenant with ``FOR UPDATE SKIP LOCKED`` and a
  conditional update, bounded per call; two workers never claim the same slot.
* ``finish``: a conditional update from ``claimed`` to ``done``, ``failed`` or ``skipped`` with an
  outcome; a second finish changes nothing.

The one writer of ``refresh_slots``. GUARANTEE(test_one_slot_one_execution)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text


@dataclass(frozen=True)
class Slot:
    """A claimed slot."""

    id: UUID
    account_id: UUID
    kind: str
    period: str


async def ensure(s: Any, *, account_id: UUID, kind: str, period: str, due_at: datetime) -> bool:
    """True when this call created the slot."""
    row = (
        await s.execute(
            text(
                "INSERT INTO refresh_slots (account_id, kind, period, due_at) VALUES (:a, :k, :p,"
                " :d)"
                " ON CONFLICT (tenant_id, account_id, kind, period) DO NOTHING RETURNING id"
            ),
            {"a": account_id, "k": kind, "p": period, "d": due_at},
        )
    ).first()
    return row is not None


async def claim(s: Any, *, now: datetime, worker: str, limit: int) -> list[Slot]:
    """Claim up to ``limit`` due pending slots (oldest due first)."""
    rows = (
        await s.execute(
            text(
                "UPDATE refresh_slots SET status = 'claimed', claimed_at = :now, claimed_by = :w"
                " WHERE id IN (SELECT id FROM refresh_slots WHERE status = 'pending' AND due_at"
                " <= :now"
                " ORDER BY due_at, id LIMIT :n FOR UPDATE SKIP LOCKED) AND status = 'pending'"
                " RETURNING id, account_id, kind, period"
            ),
            {"now": now, "w": worker, "n": limit},
        )
    ).all()
    from obs.setup import record

    record("refresh_scheduler_heartbeat", 1, {})
    return sorted(
        (Slot(r.id, r.account_id, r.kind, r.period) for r in rows),
        key=lambda x: (x.period, str(x.id)),
    )


async def recover(s: Any, *, now: datetime, lease: timedelta, limit: int = 100) -> dict[str, int]:
    """Slots left ``claimed`` by a dead worker past the lease: ``done`` (outcome ``recovered``)
    when their run was recorded (the unique slot on ``refresh_runs`` proves it ran once), else
    released to ``pending`` so the next tick runs them once."""
    stale = (
        await s.execute(
            text(
                "SELECT s.id, EXISTS (SELECT 1 FROM refresh_runs r WHERE r.slot_id = s.id) AS ran"
                " FROM refresh_slots s WHERE s.status = 'claimed' AND s.claimed_at < :cut"
                " ORDER BY s.claimed_at, s.id LIMIT :n FOR UPDATE SKIP LOCKED"
            ),
            {"cut": now - lease, "n": limit},
        )
    ).all()
    out = {"recovered": 0, "released": 0}
    for r in stale:  # LOOP(slot recovery): cap=limit stale slots; exit=end
        if r.ran:
            await s.execute(
                text(
                    "UPDATE refresh_slots SET status = 'done', outcome = 'recovered',"
                    " finished_at = :now WHERE id = :i AND status = 'claimed'"
                ),
                {"now": now, "i": r.id},
            )
            out["recovered"] += 1
        else:
            await s.execute(
                text(
                    "UPDATE refresh_slots SET status = 'pending', claimed_at = NULL,"
                    " claimed_by = NULL WHERE id = :i AND status = 'claimed'"
                ),
                {"i": r.id},
            )
            out["released"] += 1
    return out


async def finish(s: Any, slot_id: UUID, *, status: str, outcome: str, now: datetime) -> bool:
    """True when this call finished the slot."""
    if status not in ("done", "failed", "skipped"):
        raise ValueError("unknown final status")
    row = (
        await s.execute(
            text(
                "UPDATE refresh_slots SET status = :st, outcome = :o, finished_at = :now"
                " WHERE id = :i AND status = 'claimed' RETURNING id"
            ),
            {"st": status, "o": outcome, "now": now, "i": slot_id},
        )
    ).first()
    return row is not None
