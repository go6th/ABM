"""The notification outbox (M10): written in the event's own transaction, relayed at least once.

* ``enqueue`` inserts one outbox row in the caller's transaction (so a notification exists if and
  only if its event committed); the ``(tenant, dedupe_key)`` unique key makes a duplicate event
  or a race insert nothing.
* Content comes **only** from the templates below and their allow-listed parameters (identifiers,
  small integers and short codes). Tenant-audience templates carry no provider name, no provider
  code and no amount (D-04, D-30); links are console paths that carry no bearer power (a person
  still signs in and steps up, M5).
* ``relay`` delivers undelivered rows through a ``NotificationSender``: every attempt is an
  insert-only delivery row; a failure is retried on a later relay pass up to
  ``notify_max_attempts``, then ``gave_up`` is recorded. A delivery failure never raises into a
  run (it is recorded and the run goes on). The consumer is idempotent: a message with a
  ``delivered`` or ``gave_up`` row is never sent again (unique index).

GUARANTEE(test_outbox_dedupes_and_relays_at_least_once_with_bounded_retries): M10.
GUARANTEE(test_tenant_notifications_pass_the_client_output_scan): no provider, no amount.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spend.errors import SpendError

SAFE = re.compile(r"^[A-Za-z0-9_:.-]{1,64}$")


@dataclass(frozen=True)
class Template:
    """One template: its audience, allowed parameters, subject and body."""

    audience: str
    params: frozenset[str]
    subject: str
    body: str


TEMPLATES: dict[str, Template] = {
    "plan_usage_near_limit": Template(
        "tenant",
        frozenset({"dimension", "percent"}),
        "Your plan allowance is nearly used",
        "This period you have used {percent} percent of your plan's {dimension} allowance. "
        "An admin can see the details under Usage.",
    ),
    "monitoring_budget_near_limit": Template(
        "tenant",
        frozenset({"percent"}),
        "Your monitoring allowance is nearly used",
        "This month you have used {percent} percent of your monitoring allowance. Monitoring of "
        "lower-priority accounts is reduced first; an admin can see the details under Usage.",
    ),
    "spend_soft_limit": Template(
        "operator",
        frozenset({"run_id", "cap_version"}),
        "Run {run_id}: soft spend limit reached",
        "Run {run_id} reached the soft limit of cap version {cap_version}. New paid work is "
        "limited to High rows. Open the run in the operator console for the breakdown.",
    ),
    "run_spend_near_limit": Template(
        "tenant",
        frozenset({"run_id"}),
        "Your research run is close to its agreed limit",
        "Run {run_id} is close to the limit agreed for it. The most important rows continue. "
        "Details are in the console at /runs/{run_id}.",
    ),
    "spend_pause_opened": Template(
        "operator",
        frozenset({"run_id", "request_id", "cap_version"}),
        "Run {run_id} is paused for a cap approval",
        "Run {run_id} reached cap version {cap_version} and is paused (request {request_id}). "
        "An approver can approve or decline it after signing in.",
    ),
    "run_waiting_for_approval": Template(
        "tenant",
        frozenset({"run_id", "request_id"}),
        "Your research run is waiting for approval",
        "Run {run_id} needs an approval to continue. Sign in and open "
        "/runs/{run_id}/approvals/{request_id} to approve or decline it.",
    ),
    "spend_pause_decided": Template(
        "operator",
        frozenset({"run_id", "request_id", "decision"}),
        "Run {run_id}: pause {decision}",
        "The pause request {request_id} of run {run_id} was {decision}.",
    ),
    "provider_credits_exhausted": Template(
        "operator",
        frozenset({"run_id", "provider_code", "intent_id"}),
        "Run {run_id}: provider {provider_code} is out of credits",
        "Provider {provider_code} reported an empty balance during run {run_id}. The affected "
        "rows are Blocked. Upload a pre-pulled file for intent {intent_id} to fill them.",
    ),
    "upload_result": Template(
        "operator",
        frozenset({"run_id", "intent_id", "outcome", "reason_code"}),
        "Run {run_id}: upload {outcome}",
        "The file for intent {intent_id} of run {run_id} was {outcome} ({reason_code}).",
    ),
}


class NotifyError(SpendError):
    """A template, audience or parameter outside the allow-list."""


class NotificationSender(Protocol):
    """Delivers one rendered message (production: ``NotConfigured`` until a channel exists)."""

    async def send(self, audience: str, subject: str, body: str, dedupe_key: str) -> None:
        """Deliver once; raise on failure."""
        ...


def check(template: str, audience: str, params: Mapping[str, Any]) -> dict[str, str]:
    """The validated parameters as strings (``NotifyError`` on anything outside the allow-list)."""
    spec = TEMPLATES.get(template)
    if spec is None:
        raise NotifyError(f"unknown template {template}")
    if spec.audience != audience:
        raise NotifyError(f"{template} is for the {spec.audience} audience")
    if set(params) != spec.params:
        raise NotifyError(f"{template} takes exactly {sorted(spec.params)}")
    out: dict[str, str] = {}
    for key, value in params.items():
        rendered = str(value)
        if isinstance(value, bool) or not SAFE.match(rendered):
            raise NotifyError(f"{key} is not an identifier or a short code")
        out[key] = rendered
    return out


def render(template: str, params: Mapping[str, str]) -> tuple[str, str]:
    """Subject and body from the template (only its own parameters are substituted)."""
    spec = TEMPLATES[template]
    return spec.subject.format(**params), spec.body.format(**params)


async def enqueue(
    s: AsyncSession,
    run_id: UUID | None,
    dedupe_key: str,
    audience: str,
    template: str,
    params: Mapping[str, Any],
    now: datetime,
) -> bool:
    """One outbox row in the caller's transaction; False when the dedupe key already exists."""
    clean = check(template, audience, params)

    row = (
        await s.execute(
            text(
                "INSERT INTO notification_outbox (run_id, dedupe_key, audience, template,"
                " params_json, created_at) VALUES (:r, :k, :a, :t, CAST(:p AS jsonb), :at)"
                " ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING id"
            ),
            {
                "r": run_id,
                "k": dedupe_key[:200],
                "a": audience,
                "t": template,
                "p": json.dumps(clean, sort_keys=True),
                "at": now,
            },
        )
    ).first()
    return row is not None


@dataclass(frozen=True)
class RelayResult:
    """What one relay pass did."""

    delivered: int
    failed: int
    gave_up: int


async def relay(
    txn: Any,
    tenant_id: UUID,
    sender: NotificationSender,
    now: datetime,
    max_attempts: int,
) -> RelayResult:
    """One bounded relay pass over the tenant's undelivered messages (see the module docstring).
    Pending rows are read in one short transaction; each send happens outside any transaction and
    each outcome is one insert in its own transaction."""
    async with txn(tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT o.id, o.audience, o.template, o.params_json, o.dedupe_key,"
                    " (SELECT count(*) FROM notification_deliveries d WHERE d.outbox_id = o.id)"
                    "   AS attempts"
                    " FROM notification_outbox o"
                    " WHERE NOT EXISTS (SELECT 1 FROM notification_deliveries d"
                    "   WHERE d.outbox_id = o.id AND d.outcome IN ('delivered', 'gave_up'))"
                    " ORDER BY o.created_at, o.id LIMIT 100"
                )
            )
        ).all()
    delivered = failed = gave_up = 0
    # LOOP(notification relay): cap=100 messages per pass, one attempt each, max_attempts per
    # message over passes; exit=delivered, failed (retried next pass), gave_up
    for row in rows:
        attempt = int(row.attempts) + 1
        subject, body = render(row.template, row.params_json)
        outcome, error = "delivered", None
        try:
            await sender.send(row.audience, subject, body, row.dedupe_key)
        except Exception as exc:  # recorded, never raised into a run (M10)
            outcome, error = "failed", type(exc).__name__
        if outcome == "failed" and attempt >= max_attempts:
            outcome = "gave_up"
        async with txn(tenant_id) as s:
            await s.execute(
                text(
                    "INSERT INTO notification_deliveries (outbox_id, attempt, outcome, error_class,"
                    " at) VALUES (:o, :n, :out, :e, :at) ON CONFLICT DO NOTHING"
                ),
                {"o": row.id, "n": attempt, "out": outcome, "e": error, "at": now},
            )
        delivered += outcome == "delivered"
        failed += outcome == "failed"
        gave_up += outcome == "gave_up"
    return RelayResult(delivered, failed, gave_up)
