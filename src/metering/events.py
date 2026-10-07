"""Usage events (MT1): insert-only, one row per (kind, subject) through the unique idempotency
key, written in the caller's transaction (the action and its usage commit together). A retried or
replayed action adds nothing: ``run_settled`` is recorded once per run whatever the settlement
path does again. The one writer of ``usage_events``.

GUARANTEE(test_usage_events_are_idempotent_and_settlement_emits_once)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text

Kind = Literal["account_added", "run_started", "run_settled", "refresh_run", "export_created"]


def period_of(at: datetime) -> str:
    """The usage period (UTC calendar month, D-67 proposal) of a time."""
    return at.astimezone(UTC).strftime("%Y-%m")


async def record(s: Any, kind: Kind, subject_id: object, at: datetime, units: int = 1) -> bool:
    """One usage event; False when this (kind, subject) was already recorded."""
    subject = str(subject_id)
    row = (
        await s.execute(
            text(
                "INSERT INTO usage_events (kind, subject_id, idempotency_key, units, period, at)"
                " VALUES (:k, :s, :key, :u, :p, :at)"
                " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
            ),
            {
                "k": kind,
                "s": subject,
                "key": f"{kind}:{subject}",
                "u": units,
                "p": period_of(at),
                "at": at,
            },
        )
    ).first()
    return row is not None
