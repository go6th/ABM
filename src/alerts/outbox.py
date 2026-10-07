"""The outbox relay (AL6): at-least-once delivery with idempotent consumers keyed by the outbox
id (the delivery event id), bounded retries, and no transaction held across a channel call.

One pass: claim up to ``limit`` due pending rows (``FOR UPDATE SKIP LOCKED``; the claim bumps
``attempts`` and pushes ``next_attempt_at`` so a crashed worker's row comes back later), commit;
call each channel outside the transaction; then mark ``sent`` (with ``delivered_at``) or schedule
the next attempt from the 24 h exponential schedule, or ``failed`` once the schedule is exhausted
(operator-visible). A delivery failure never blocks a refresh. Digest rows are delivered by the
digest job.

The one writer of ``alert_outbox`` state.

GUARANTEE(test_the_outbox_delivers_once_per_event_id_and_retries_within_24h)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.safety.client_output import assert_client_safe
from obs.setup import record
from signals.webhooks import retry_schedule, sign

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[Any]]
LEASE = timedelta(minutes=10)


@dataclass
class Channels:
    """The ports (``mailer`` is ``tenancy.ports.Mailer``)."""

    mailer: Any
    slack: Any
    webhook: Any
    webhook_key: bytes | None = None


@dataclass(frozen=True)
class PassResult:
    """One relay pass."""

    sent: int
    retried: int
    failed: int


async def relay(
    txn: TxnFactory, tenant: UUID, ch: Channels, *, now: datetime, limit: int = 50
) -> PassResult:
    """One bounded relay pass for a tenant."""
    async with txn(tenant) as s:
        rows = (
            await s.execute(
                text(
                    "UPDATE alert_outbox SET attempts = attempts + 1, next_attempt_at = :lease"
                    " WHERE id IN (SELECT id FROM alert_outbox WHERE status = 'pending'"
                    " AND channel <> 'digest' AND next_attempt_at <= :now ORDER BY"
                    " next_attempt_at, id"
                    " LIMIT :n FOR UPDATE SKIP LOCKED) RETURNING id, alert_id, channel, recipient,"
                    " attempts"
                ),
                {"now": now, "lease": now + LEASE, "n": limit},
            )
        ).all()
        alerts = {}
        for r in rows:
            a = (
                await s.execute(
                    text(
                        "SELECT subject, body, signal_detected_at, event_id FROM alerts WHERE id"
                        " = :a"
                    ),
                    {"a": r.alert_id},
                )
            ).one()
            alerts[r.id] = a
    sent = retried = failed = 0
    for r in sorted(rows, key=lambda x: str(x.id)):  # LOOP(outbox relay): cap=limit; exit=end
        a = alerts[r.id]
        error: str | None = None
        try:
            assert_client_safe([a.subject, a.body])
            if r.channel == "email":
                await ch.mailer.send(
                    r.recipient,
                    "signal_alert",
                    {"subject": a.subject, "body": a.body, "event_id": str(r.id)},
                )
            elif r.channel == "slack":
                await ch.slack.post(
                    r.recipient, f"{a.subject}\n{a.body}", idempotency_key=str(r.id)
                )
            elif r.channel == "webhook":
                if ch.webhook_key is None:
                    raise PermissionError("no webhook key")
                body = json.dumps(
                    {"event_id": str(r.id), "alert_event": str(a.event_id), "subject": a.subject},
                    sort_keys=True,
                ).encode()
                ts = int(now.timestamp())
                status = await ch.webhook.post(
                    body,
                    {
                        "X-Signature": sign(ch.webhook_key, ts, body),
                        "X-Timestamp": str(ts),
                        "X-Event-Id": str(r.id),
                    },
                )
                if status >= 300:
                    error = "http_status"
        except Exception as e:  # a channel failure is recorded, never raised into a refresh
            error = "not_configured" if type(e).__name__ == "NotConfigured" else "channel_error"
        async with txn(tenant) as s:
            if error is None:
                await s.execute(
                    text(
                        "UPDATE alert_outbox SET status = 'sent', delivered_at = :now, last_error ="
                        " NULL WHERE id = :i AND status = 'pending'"
                    ),
                    {"now": now, "i": r.id},
                )
                sent += 1
                record("alert_deliveries_total", 1, {"channel": r.channel, "outcome": "sent"})
                record(
                    "alert_delivery_latency_s",
                    (now - a.signal_detected_at).total_seconds(),
                    {"channel": r.channel},
                )
                continue
            schedule = retry_schedule()
            n = int(r.attempts)
            if n <= len(schedule):
                delay = schedule[n - 1] - (schedule[n - 2] if n >= 2 else 0)
                await s.execute(
                    text(
                        "UPDATE alert_outbox SET next_attempt_at = :t, last_error = :e"
                        " WHERE id = :i AND status = 'pending'"
                    ),
                    {"t": now + timedelta(seconds=delay), "e": error, "i": r.id},
                )
                retried += 1
                record("alert_deliveries_total", 1, {"channel": r.channel, "outcome": "retried"})
            else:
                await s.execute(
                    text(
                        "UPDATE alert_outbox SET status = 'failed', last_error = :e"
                        " WHERE id = :i AND status = 'pending'"
                    ),
                    {"e": error, "i": r.id},
                )
                failed += 1
                record("alert_deliveries_total", 1, {"channel": r.channel, "outcome": "failed"})
    return PassResult(sent, retried, failed)


async def deliver_in_app(s: Any, *, now: datetime) -> int:
    """In-app rows are delivered by being visible in the inbox: mark them sent."""
    rows = (
        await s.execute(
            text(
                "UPDATE alert_outbox SET status = 'sent', delivered_at = :now WHERE channel ="
                " 'in_app'"
                " AND status = 'pending' RETURNING id"
            ),
            {"now": now},
        )
    ).all()
    return len(rows)


async def freshness_rows(s: Any) -> list[tuple[datetime, datetime | None]]:
    """(signal_detected_at, first delivery) per alert, for ``alerts.freshness``."""
    rows = (
        await s.execute(
            text(
                "SELECT a.signal_detected_at, min(o.delivered_at) AS d FROM alerts a"
                " LEFT JOIN alert_outbox o ON o.alert_id = a.id AND o.status = 'sent'"
                " GROUP BY a.id, a.signal_detected_at"
            )
        )
    ).all()
    return [(r.signal_detected_at, r.d) for r in rows]
