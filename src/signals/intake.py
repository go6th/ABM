"""Monitor intake (SM1 to SM4): monitor runs with typed outcomes, inbound webhook receipts and
counted drops.

* ``run_monitor``: one poll of one monitor kind; ``NotConfigured`` is outcome ``not_configured``
  and a rate-limit or source failure is ``blocked`` with a reason, so the tracker row becomes
  Blocked, never "no change". A throttled account (CG2) is recorded ``throttled`` without a call.
* ``receive_webhook``: verifies with ``signals.webhooks`` (unknown source, stale, bad signature,
  replay), records the receipt (unique per source and event id), stores only a quarantine
  reference of the payload; nothing in it is trusted. Refusals are counted drops.

The one writer of ``monitor_runs``, ``webhook_receipts``, ``intake_drops`` and
``monitor_configs``.

GUARANTEE(test_monitors_and_webhooks_end_in_typed_outcomes)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.platform.ports import NotConfigured
from signals import webhooks
from signals.registry import Monitor, Observation


class SourceFailed(RuntimeError):
    """A provider refused or failed (rate limited, outage); carries a fixed reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class MonitorResult:
    """A poll's outcome."""

    outcome: str
    reason: str | None
    observations: tuple[Observation, ...]


async def set_kinds(
    s: Any, *, account_id: UUID, kinds: Sequence[str], set_by: str, now: datetime
) -> int:
    """A new monitor-set version for an account."""
    version = int(
        (
            await s.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM monitor_configs WHERE account_id ="
                    " :a"
                ),
                {"a": account_id},
            )
        ).scalar_one()
    )
    await s.execute(
        text(
            "INSERT INTO monitor_configs (account_id, version, kinds, set_by, created_at)"
            " VALUES (:a, :v, :k, :by, :now)"
        ),
        {"a": account_id, "v": version, "k": sorted(set(kinds)), "by": set_by, "now": now},
    )
    return version


async def _log(
    s: Any,
    account_id: UUID,
    kind: str,
    result: MonitorResult,
    units: int,
    refresh_run_id: UUID | None,
    now: datetime,
) -> None:
    await s.execute(
        text(
            "INSERT INTO monitor_runs (account_id, kind, refresh_run_id, outcome, reason,"
            " observations, units, at) VALUES (:a, :k, :r, :o, :why, :n, :u, :now)"
        ),
        {
            "a": account_id,
            "k": kind,
            "r": refresh_run_id,
            "o": result.outcome,
            "why": result.reason,
            "n": len(result.observations),
            "u": units,
            "now": now,
        },
    )


async def run_monitor(
    s: Any,
    monitor: Monitor,
    *,
    account_id: UUID,
    domain: str,
    since: datetime | None,
    refresh_run_id: UUID | None,
    units: int,
    throttled: bool,
    now: datetime,
) -> MonitorResult:
    """One poll with its recorded outcome (the caller holds no transaction across the call in
    production; here the poll is a port call made before the insert)."""
    if throttled:
        result = MonitorResult("throttled", "monitoring_reduced", ())
        units = 0
    else:
        try:
            obs = tuple(await monitor.poll(domain, since))
            result = MonitorResult("ok", None, obs)
        except NotConfigured:
            result = MonitorResult("not_configured", "not_configured", ())
            units = 0
        except SourceFailed as e:
            result = MonitorResult("blocked", e.reason, ())
    await _log(s, account_id, monitor.kind, result, units, refresh_run_id, now)
    return result


async def drop(s: Any, *, account_id: UUID, kind: str, reason: str, now: datetime) -> None:
    """A counted drop."""
    await s.execute(
        text("INSERT INTO intake_drops (account_id, kind, reason, at) VALUES (:a, :k, :r, :now)"),
        {"a": account_id, "k": kind, "r": reason, "now": now},
    )


async def receive_webhook(
    s: Any,
    *,
    keys: Mapping[str, bytes],
    source: str,
    timestamp: int,
    body: bytes,
    signature: str,
    event_id: str,
    now: datetime,
    account_id: UUID,
    payload_ref: str,
) -> webhooks.Verdict:
    """Verify, record and hand off one inbound delivery."""
    seen_row = (
        await s.execute(
            text("SELECT 1 FROM webhook_receipts WHERE source = :s AND event_id = :e"),
            {"s": source, "e": event_id},
        )
    ).first()
    seen = {f"{source}:{event_id}"} if seen_row else set()
    verdict = webhooks.verify(
        keys=keys,
        source=source,
        timestamp=timestamp,
        body=body,
        signature=signature,
        event_id=event_id,
        now=int(now.timestamp()),
        seen=seen,
    )
    if verdict == "ok":
        inserted = (
            await s.execute(
                text(
                    "INSERT INTO webhook_receipts (source, event_id, verdict, payload_ref,"
                    " received_at)"
                    " VALUES (:s, :e, 'ok', :p, :now) ON CONFLICT (tenant_id, source, event_id)"
                    " DO NOTHING RETURNING id"
                ),
                {"s": source, "e": event_id, "p": payload_ref, "now": now},
            )
        ).first()
        if inserted is None:
            verdict = "replay"
    if verdict != "ok":
        await drop(s, account_id=account_id, kind="webhook", reason=verdict, now=now)
    return verdict
