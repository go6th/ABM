"""Step timings from durable events (O7), so they exist for unsampled traces and after a
collector outage. Per run: queue wait (created to first slice), time awaiting approval, and the
unit schedule from ``work_unit_events`` (``parallel.critical_path.analyse``: one implementation).
A source that does not exist yields ``SourceMissing``, never a guess.

GUARANTEE(test_timings_come_from_events_and_report_missing_sources): O7.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from obs.errors import SourceMissing
from parallel.critical_path import ScheduleReport, analyse
from parallel.models import EventKind, UnitEvent, WorkUnit

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class RunTimeline:
    """What the operator timeline shows."""

    run_id: str
    created_at: datetime
    finished_at: datetime | None
    status: str
    units: ScheduleReport
    missing: tuple[str, ...]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (pure)."""
    if not values:
        return None
    ordered = sorted(values)
    import math

    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


async def _exists(s: AsyncSession, table: str) -> bool:
    value: Any = (await s.execute(text("SELECT to_regclass(:t)"), {"t": table})).scalar_one()
    return value is not None


async def timeline(txn: TxnFactory, tenant_id: UUID, run_id: UUID) -> RunTimeline:
    """The run's event-derived timeline (``LookupError`` when the run is not this tenant's)."""
    missing: list[str] = []
    async with txn(tenant_id) as s:
        run = (
            await s.execute(
                text("SELECT created_at, finished_at, status FROM account_runs WHERE id = :r"),
                {"r": run_id},
            )
        ).first()
        if run is None:
            raise LookupError("run not found for this tenant")
        units: list[WorkUnit] = []
        events: list[UnitEvent] = []
        if await _exists(s, "work_unit_events"):
            for r in (
                await s.execute(
                    text(
                        "SELECT unit_key, unit_type, body, depends_on FROM work_units"
                        " WHERE run_id = :r ORDER BY unit_key"
                    ),
                    {"r": run_id},
                )
            ).all():
                units.append(
                    WorkUnit(r.unit_key, r.unit_type, r.body, depends_on=tuple(r.depends_on))
                )
            for r in (
                await s.execute(
                    text(
                        "SELECT unit_key, event, at, reason, lease_wait_s FROM work_unit_events"
                        " WHERE run_id = :r ORDER BY id"
                    ),
                    {"r": run_id},
                )
            ).all():
                events.append(
                    UnitEvent(
                        r.unit_key,
                        EventKind(r.event),
                        r.at,
                        r.reason,
                        lease_wait_s=float(r.lease_wait_s),
                    )
                )
        else:
            missing.append("work_unit_events")
        for table in ("stage_attempts", "run_events"):
            if not await _exists(s, table):
                missing.append(table)  # sources of later work packages (review and resume)
    return RunTimeline(
        str(run_id),
        run.created_at,
        run.finished_at,
        run.status,
        analyse(units, events),
        tuple(missing),
    )


def require(source: str, present: bool) -> None:
    """``SourceMissing`` when a derivation's source is absent."""
    if not present:
        raise SourceMissing(source)
