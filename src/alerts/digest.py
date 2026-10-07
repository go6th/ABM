"""Weekly digest assembly (AL4): bounded and deterministic. Items are ordered by score (high
first), then signal time, then fingerprint; at most ``limit`` items, the rest counted as "and N
more in the app". Sensitive items keep their fixed wording; Leads keep their label.

``build`` runs once per user and ISO week (``digest_runs`` is unique): it gathers the user's pending
digest deliveries, assembles them and marks them sent; the caller sends the returned body through
the mailer port after the transaction commits.
The one writer of ``digest_runs``.

GUARANTEE(test_digests_are_bounded_and_deterministic)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from account_intelligence.safety.client_output import assert_client_safe
from alerts import lint


@dataclass(frozen=True)
class Item:
    """One digest line's inputs (already rendered by ``alerts.templates``)."""

    fingerprint: str
    score: int
    signal_at: datetime
    line: str


def assemble(items: Sequence[Item], *, limit: int) -> tuple[list[str], int]:
    """The lines and the count left out."""
    ordered = sorted(items, key=lambda i: (-i.score, i.signal_at, i.fingerprint))
    seen: set[str] = set()
    lines: list[str] = []
    for it in ordered:  # LOOP(digest assembly): cap=len(items); exit=end
        if it.fingerprint in seen:
            continue
        seen.add(it.fingerprint)
        lines.append(lint.check(it.line))
    shown = lines[:limit]
    return shown, len(lines) - len(shown)


@dataclass(frozen=True)
class Built:
    """A digest ready to send AFTER the transaction commits (no transaction across the mailer)."""

    body: str
    items: int
    more: int


async def build(s: Any, *, user_id: str, period: str, limit: int, now: datetime) -> Built | None:
    """Claim and assemble one digest; None when this user's digest for the period exists."""
    rows = (
        await s.execute(
            text(
                "SELECT o.id, a.fingerprint, a.score, a.signal_detected_at, a.subject, a.body"
                " FROM alert_outbox o JOIN alerts a ON a.id = o.alert_id WHERE o.recipient = :u"
                " AND o.channel = 'digest' AND o.status = 'pending' ORDER BY o.id LIMIT 1000"
            ),
            {"u": user_id},
        )
    ).all()
    lines, more = assemble(
        [
            Item(r.fingerprint, int(r.score), r.signal_detected_at, f"{r.subject}: {r.body}")
            for r in rows
        ],
        limit=limit,
    )
    claimed = (
        await s.execute(
            text(
                "INSERT INTO digest_runs (user_id, period, items, more, created_at)"
                " VALUES (:u, :p, :n, :m, :now) ON CONFLICT DO NOTHING RETURNING user_id"
            ),
            {"u": user_id, "p": period, "n": len(lines), "m": more, "now": now},
        )
    ).first()
    if claimed is None:
        return None
    for r in rows:
        await s.execute(
            text(
                "UPDATE alert_outbox SET status = 'sent', delivered_at = :now WHERE id = :i"
                " AND status = 'pending'"
            ),
            {"now": now, "i": r.id},
        )
    body = "\n".join(lines) + (f"\nand {more} more in the app" if more else "")
    assert_client_safe([body])
    return Built(body, len(lines), more)
