"""In-memory fakes for parallel execution tests (synthetic): the unit event store, the phase hooks,
a virtual executor clock and strict stub providers that enforce their own concurrency limit and
count violations attributable to our scheduling."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from parallel.executor import StoredEvent
from parallel.models import FundingMode, UnitEvent, WorkUnit

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class MemoryEventStore:
    """``EventStore`` in memory; ``fail_after`` simulates a worker killed after N appends."""

    def __init__(self) -> None:
        self.units: dict[str, WorkUnit] = {}
        self.events: list[StoredEvent] = []
        self.fail_after: int | None = None

    async def define(self, units: Sequence[WorkUnit]) -> None:
        for u in units:
            self.units.setdefault(u.key, u)

    async def load(self, after: int = 0) -> list[StoredEvent]:
        await asyncio.sleep(0)
        return [e for e in self.events if e.seq > after]

    async def append(self, event: UnitEvent, result: Mapping[str, Any] | None = None) -> None:
        if self.fail_after is not None and len(self.events) >= self.fail_after:
            raise KeyboardInterrupt("worker killed")  # a BaseException, like a SIGKILL'd task
        terminal = {"finished", "failed_terminal", "blocked"}
        if event.event.value in terminal and any(
            e.event.unit_key == event.unit_key and e.event.event.value in terminal
            for e in self.events
        ):
            return  # the unique terminal index: a duplicate inserts nothing
        self.events.append(
            StoredEvent(event, dict(result) if result is not None else None, len(self.events) + 1)
        )

    def kinds(self, key: str) -> list[str]:
        return [e.event.event.value for e in self.events if e.event.unit_key == key]


@dataclass
class FakeHooks:
    """Funding script, cap-denial and unrun records, cancellation flag."""

    modes: list[FundingMode] = field(default_factory=list)
    cap_denied: list[str] = field(default_factory=list)
    unrun: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    async def funding(self) -> FundingMode:
        if self.cap_denied and not self.modes:
            return FundingMode.PAUSED
        return self.modes.pop(0) if len(self.modes) > 1 else (self.modes or [FundingMode.NORMAL])[0]

    async def on_cap_denied(self, unit: WorkUnit) -> None:
        self.cap_denied.append(unit.key)

    async def on_unrun(self, unit: WorkUnit, reason: str) -> Mapping[str, Any]:
        self.unrun.append((unit.key, reason))
        return {"coverage_updates": {f"row:{unit.key}": {"status": "not_done", "reason": reason}}}

    async def run_cancelled(self) -> bool:
        return self.cancelled


class ExecClock:
    """Wall time advancing with sleeps; monotonic follows it."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> datetime:
        return T0 + timedelta(seconds=self.t)

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


@dataclass
class StubProvider:
    """A provider that enforces its own concurrency limit and counts violations."""

    limit: int
    active: int = 0
    peak: int = 0
    violations: int = 0
    calls: int = 0

    async def call(self, seconds: float) -> None:
        self.active += 1
        self.calls += 1
        self.peak = max(self.peak, self.active)
        if self.active > self.limit:
            self.violations += 1
        try:
            await asyncio.sleep(seconds)
        finally:
            self.active -= 1
