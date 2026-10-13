"""Spend derived from the insert-only ledger (K7, K8). The SQL reads ``tool_call_events``; the
aggregation is pure (``aggregate``) so it is tested without a database.

Definitions, the same as the connector's ``ledger_sum``: a group's spend is the charge of its
``done``, ``failed`` and ``abandoned`` events plus the reservation of every attempt still open
(``started`` with no terminal event; counted until reconciled, K9). Replays are answered from
the ledger without an event, so they are **not measured** here (``None``), never 0. A pre-pulled
file consumed instead of a call (``cost_source='prepulled'``) is counted apart, never as paid;
a paid call the provider reported at zero cost is counted (``zero_charge_calls``) for review.

GUARANTEE(test_rollup_parts_sum_to_the_ledger_total): parts equal the whole exactly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.models import SpendLine
from spend.money import usd_to_micros

TERMINAL = frozenset({"done", "failed", "abandoned"})


@dataclass(frozen=True)
class EventRow:
    """One ledger event (money in micro-dollars)."""

    provider_code: str
    operation: str
    route: str | None
    args_hash: str
    attempt: int
    event: str
    reserved_micros: int
    charged_micros: int
    credits: Decimal | None
    cost_source: str | None
    possible_duplicate: bool


def open_attempts(rows: Sequence[EventRow]) -> list[EventRow]:
    """``started`` events whose attempt has no terminal event."""
    closed = {(r.args_hash, r.attempt) for r in rows if r.event in TERMINAL}
    return [r for r in rows if r.event == "started" and (r.args_hash, r.attempt) not in closed]


def aggregate(rows: Sequence[EventRow], key: Callable[[EventRow], str]) -> tuple[SpendLine, ...]:
    """Spend lines grouped by ``key`` (sorted by key)."""
    opened = {id(r) for r in open_attempts(rows)}
    groups: dict[str, list[EventRow]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    lines = []
    for name in sorted(groups):
        rs = groups[name]
        # a pre-pulled file or a call adopted from an earlier run (research cache) paid nothing
        done = [r for r in rs if r.event == "done" and r.cost_source not in ("prepulled", "cache")]
        prepulled = sum(1 for r in rs if r.event == "done" and r.cost_source == "prepulled")
        charged = sum(r.charged_micros for r in rs if r.event in TERMINAL)
        reserved = sum(r.reserved_micros for r in rs if id(r) in opened)
        dup = [r for r in rs if r.possible_duplicate and r.event in TERMINAL]
        known = [r.credits for r in done if r.credits is not None]
        credits: Decimal | None = sum(known, Decimal(0)) if len(known) == len(done) else None
        lines.append(
            SpendLine(
                key=name,
                paid_calls=len(done),
                replays=None,
                failed_after_charge=sum(
                    1 for r in rs if r.event == "failed" and r.charged_micros > 0
                ),
                open_reserves=sum(1 for r in rs if id(r) in opened),
                credits=credits,
                spent_micros=charged + reserved,
                unknown_cost_calls=sum(1 for r in done if r.cost_source == "unknown"),
                possible_duplicates=len(dup),
                at_risk_micros=sum(r.charged_micros for r in dup),
                prepulled_calls=prepulled,
                zero_charge_calls=sum(1 for r in done if r.charged_micros == 0),
            )
        )
    return tuple(lines)


async def ledger_events(s: AsyncSession, run_id: UUID) -> list[EventRow]:
    """Every ledger event of the run (RLS scopes it to the tenant)."""
    rows = (
        await s.execute(
            text(
                "SELECT provider_code, operation, route, args_hash, attempt, event, reserved_usd,"
                " charged_usd, credits, cost_source, possible_duplicate FROM tool_call_events"
                " WHERE run_id = :r ORDER BY id"
            ),
            {"r": run_id},
        )
    ).all()
    return [
        EventRow(
            provider_code=r.provider_code or "unknown",
            operation=r.operation or "unknown",
            route=r.route,
            args_hash=r.args_hash,
            attempt=r.attempt,
            event=r.event,
            reserved_micros=usd_to_micros(r.reserved_usd) if r.reserved_usd is not None else 0,
            charged_micros=usd_to_micros(r.charged_usd) if r.charged_usd is not None else 0,
            credits=Decimal(r.credits) if r.credits is not None else None,
            cost_source=r.cost_source,
            possible_duplicate=bool(r.possible_duplicate),
        )
        for r in rows
    ]
