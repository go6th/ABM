"""Candidate health: shared circuit state per candidate (not a ledger; R4).

``step`` is pure: a failure inside the window counts; at ``threshold`` failures the circuit opens
for ``cooldown_s``; a success closes it and resets the count; one state change per event. Stores:
``InMemoryHealth`` (tests) and ``PgHealth`` (``llm_candidate_health``, migration 0018, written by
one conditional statement per event).

GUARANTEE(test_health_opens_after_threshold_and_closes_after_success): property.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Event = Literal["failure", "success"]


@dataclass(frozen=True)
class Health:
    """One candidate's circuit."""

    failures: int = 0
    window_start: datetime | None = None
    open_until: datetime | None = None

    def is_open(self, now: datetime) -> bool:
        """Whether calls must skip this candidate now."""
        return self.open_until is not None and now < self.open_until


def step(
    h: Health, event: Event, now: datetime, threshold: int, window_s: float, cooldown_s: float
) -> Health:
    """The next state (see the module docstring)."""
    if event == "success":
        return Health()
    window = timedelta(seconds=window_s)
    if h.window_start is None or now - h.window_start > window:
        h = Health(0, now, h.open_until)
    h = replace(h, failures=h.failures + 1)
    if h.failures >= threshold:
        return Health(0, None, now + timedelta(seconds=cooldown_s))
    return h


class HealthStore(Protocol):
    """Shared circuit state."""

    async def open_circuits(self, now: datetime) -> frozenset[str]:
        """Candidates whose circuit is open now."""
        ...

    async def record(self, candidate: str, event: Event, now: datetime) -> None:
        """Apply one event."""
        ...


class InMemoryHealth:
    """One-process health store."""

    def __init__(self, threshold: int, window_s: float, cooldown_s: float) -> None:
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s
        self.state: dict[str, Health] = {}

    async def open_circuits(self, now: datetime) -> frozenset[str]:
        """Candidates whose circuit is open now."""
        return frozenset(n for n, h in self.state.items() if h.is_open(now))

    async def record(self, candidate: str, event: Event, now: datetime) -> None:
        """Apply one event."""
        self.state[candidate] = step(
            self.state.get(candidate, Health()),
            event,
            now,
            self.threshold,
            self.window_s,
            self.cooldown_s,
        )


class PgHealth:
    """``llm_candidate_health`` (global, mutable, documented as shared state; no tenant data)."""

    def __init__(
        self,
        txn: Callable[[UUID], AbstractAsyncContextManager[AsyncSession]],
        tenant_id: UUID,
        threshold: int,
        window_s: float,
        cooldown_s: float,
    ) -> None:
        self.txn, self.tenant_id = txn, tenant_id
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s

    async def open_circuits(self, now: datetime) -> frozenset[str]:
        """Candidates whose circuit is open now."""
        async with self.txn(self.tenant_id) as s:
            rows = (
                await s.execute(
                    text("SELECT candidate FROM llm_candidate_health WHERE open_until > :now"),
                    {"now": now},
                )
            ).all()
        return frozenset(str(r.candidate) for r in rows)

    async def record(self, candidate: str, event: Event, now: datetime) -> None:
        """Read, step, write back under a row lock (one change per event)."""
        async with self.txn(self.tenant_id) as s:
            await s.execute(
                text("SELECT llm_health_record(:c, :e, :now, :t, :w, :cd)"),
                {
                    "c": candidate,
                    "e": event,
                    "now": now,
                    "t": self.threshold,
                    "w": self.window_s,
                    "cd": self.cooldown_s,
                },
            )
