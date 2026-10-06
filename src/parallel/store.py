"""The Postgres unit event store (``work_units`` and ``work_unit_events``, migration 0017).

Insert-only. ``define`` writes each unit definition once (``ON CONFLICT DO NOTHING`` on the run and
key); ``append`` writes one event in its own short transaction (never held across a body, P12); a
second terminal event for a unit inserts nothing (partial unique index), so a duplicate delivery
or a racing worker cannot give a unit two outcomes. ``load`` returns the run's events in insertion
order: the executor re-derives every state from them (P9). Results stored with events are the small
reference maps of the checkpoint (64 KiB cap enforced by the table).

GUARANTEE(test_pg_event_store_round_trip_and_single_terminal): on Postgres.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from parallel.config import ParallelConfig
from parallel.executor import StoredEvent
from parallel.models import Durations, EventKind, UnitEvent, WorkUnit

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def plan_hash(units: Sequence[WorkUnit]) -> str:
    """sha256 of the canonical unit plan (recorded with every definition)."""
    body = [
        [u.key, u.unit_type, u.body, list(u.depends_on), sorted(u.soft_depends_on), list(u.routes)]
        for u in sorted(units, key=lambda x: x.key)
    ]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


class PgEventStore:
    """``EventStore`` over Postgres for one run (tenant from the run row, never an argument)."""

    def __init__(self, txn: TxnFactory, tenant_id: UUID, run_id: UUID, worker: str) -> None:
        self.txn = txn
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.worker = worker[:200]

    async def define(self, units: Sequence[WorkUnit]) -> None:
        """Write each definition once."""
        digest = plan_hash(units)
        async with self.txn(self.tenant_id) as s:
            for u in units:
                await s.execute(
                    text(
                        "INSERT INTO work_units (run_id, unit_key, unit_type, body, lane, shard,"
                        " depends_on, soft_depends_on, routes, priority, est_cost_micros,"
                        " plan_hash) VALUES (:r, :k, :t, :b, :l, :sh, :d, :sd, :ro, :p, :c, :h)"
                        " ON CONFLICT (run_id, unit_key) DO NOTHING"
                    ),
                    {
                        "r": self.run_id,
                        "k": u.key,
                        "t": u.unit_type,
                        "b": u.body,
                        "l": u.lane,
                        "sh": u.shard,
                        "d": list(u.depends_on),
                        "sd": sorted(u.soft_depends_on),
                        "ro": list(u.routes),
                        "p": u.priority,
                        "c": u.est_cost_micros,
                        "h": digest,
                    },
                )

    async def load(self, after: int = 0) -> list[StoredEvent]:
        """The run's events with ``id > after`` in insertion order."""
        async with self.txn(self.tenant_id) as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, unit_key, event, at, reason, provider_code, lease_wait_s,"
                        " error_class, result_json FROM work_unit_events WHERE run_id = :r"
                        " AND id > :after ORDER BY id"
                    ),
                    {"r": self.run_id, "after": after},
                )
            ).all()
        return [
            StoredEvent(
                UnitEvent(
                    r.unit_key,
                    EventKind(r.event),
                    r.at,
                    reason=r.reason,
                    provider_code=r.provider_code,
                    lease_wait_s=float(r.lease_wait_s or Decimal(0)),
                    error_class=r.error_class,
                ),
                dict(r.result_json) if r.result_json is not None else None,
                int(r.id),
            )
            for r in rows
        ]

    async def append(self, event: UnitEvent, result: Mapping[str, Any] | None = None) -> None:
        """Insert one event; a second terminal event of a unit inserts nothing."""
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO work_unit_events (run_id, unit_key, event, at, reason,"
                    " provider_code, lease_wait_s, error_class, result_json, worker)"
                    " VALUES (:r, :k, :e, :at, :why, :p, :w, :ec, CAST(:res AS jsonb), :wk)"
                    " ON CONFLICT (run_id, unit_key)"
                    " WHERE event IN ('finished', 'failed_terminal', 'blocked') DO NOTHING"
                ),
                {
                    "r": self.run_id,
                    "k": event.unit_key,
                    "e": event.event.value,
                    "at": event.at,
                    "why": (event.reason or None) and event.reason[:300],
                    "p": event.provider_code,
                    "w": round(event.lease_wait_s, 3),
                    "ec": event.error_class,
                    "res": None if result is None else json.dumps(result, sort_keys=True),
                    "wk": self.worker,
                },
            )


async def measured_durations(s: AsyncSession, cfg: ParallelConfig) -> Durations:
    """p95 run time per unit type from this tenant's finished units (RLS scopes the read); a type
    with fewer than ``min_samples_for_p95`` samples is left out, so the scheduler uses the flagged
    conservative fallback for it (never a guess presented as measured)."""
    rows = (
        await s.execute(
            text(
                "WITH spans AS ("
                " SELECT u.unit_type, f.at - max(st.at) AS took"
                "   FROM work_unit_events f"
                "   JOIN work_units u ON u.run_id = f.run_id AND u.unit_key = f.unit_key"
                "   JOIN work_unit_events st ON st.run_id = f.run_id AND st.unit_key = f.unit_key"
                "    AND st.event = 'started' AND st.id < f.id"
                "  WHERE f.event = 'finished'"
                "  GROUP BY u.unit_type, f.id, f.at)"
                " SELECT unit_type, count(*) AS n,"
                "  percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM took)) AS p95"
                " FROM spans GROUP BY unit_type"
            )
        )
    ).all()
    measured = {str(r.unit_type): float(r.p95) for r in rows if int(r.n) >= cfg.min_samples_for_p95}
    return Durations(measured, cfg.unmeasured_p95_s)
