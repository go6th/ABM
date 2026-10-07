"""``WorldScript``: a scripted, evolving external world for Phase 4 (SYNTHETIC only).

A script is a list of ``Happening`` rows: from week ``start`` to week ``end`` (exclusive; None =
forever) an item exists for an account and monitor kind, reported by one or more outlets. Weeks
can also carry outages (the source fails) or endless rate limits for a kind. ``FakeMonitor``
answers polls from the script at the virtual clock's week, so a months-long simulation is exact
and replayable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from signals.intake import SourceFailed
from signals.registry import MonitorKind, Observation

EPOCH = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)  # a Monday: week 0


def week_of(at: datetime) -> int:
    """The script week of an instant."""
    return (at - EPOCH).days // 7


@dataclass(frozen=True)
class Happening:
    """Something true in the world for a span of weeks."""

    account: str
    kind: MonitorKind
    item_key: str
    event_type: str
    entity: str
    title: str
    start: int
    end: int | None = None
    outlets: tuple[str, ...] = ("https://news-a.example",)
    tier: str = "t2"


@dataclass
class WorldScript:
    """The script, plus failure weeks per (kind, week)."""

    happenings: list[Happening] = field(default_factory=list)
    outages: set[tuple[MonitorKind, int]] = field(default_factory=set)
    rate_limited: set[MonitorKind] = field(default_factory=set)
    calls: list[tuple[str, MonitorKind, int]] = field(default_factory=list)

    def at(self, account: str, kind: MonitorKind, week: int) -> list[Observation]:
        """What a poll sees (one observation per outlet)."""
        out = []
        for h in self.happenings:
            if h.account != account or h.kind != kind:
                continue
            if h.start <= week and (h.end is None or week < h.end):
                for outlet in h.outlets:
                    out.append(
                        Observation(
                            kind,
                            h.item_key,
                            h.event_type,
                            h.entity,
                            h.title,
                            EPOCH + timedelta(weeks=h.start),
                            f"{outlet}/{h.item_key}",
                            h.tier,
                            f"quarantine://{account}/{h.item_key}/{outlet}",
                        )
                    )
        return sorted(out, key=lambda o: (o.item_key, o.source_url))


@dataclass
class FakeMonitor:
    """A monitor answering from the world at the clock's week."""

    kind: MonitorKind
    world: WorldScript
    clock: object

    async def poll(self, account_domain: str, since: datetime | None) -> Sequence[Observation]:
        """Observations, or SourceFailed for an outage or a rate limit."""
        week = week_of(self.clock.now())  # type: ignore[attr-defined]
        self.world.calls.append((account_domain, self.kind, week))
        if self.kind in self.world.rate_limited:
            raise SourceFailed("source_rate_limited")
        if (self.kind, week) in self.world.outages:
            raise SourceFailed("source_unavailable")
        return self.world.at(account_domain, self.kind, week)
