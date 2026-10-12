"""Pause for approval instead of failing (D-45; M3, M4, M5, M11).

**The request is computed in code** (``compute_request``, pure, integer micro-dollars): the
shortfall from the upper-bound estimates of the units the cap blocked (a unit with no estimate is
counted at the configured sample hold and listed as unknown, never as zero), a suggested new cap
rounded UP to the configured step and limited by any tenant ceiling, the rows at risk, and the
spend so far with its breakdown. Every number carries its base.

**Opening** (``open_pause``, in the caller's transaction): exactly one open request per cap
version (a partial unique index; a duplicate delivery or a race returns the existing request and
inserts nothing); a run past ``max_pauses_per_run`` or whose ceiling leaves no room is declined
by policy at once (recorded, the run then ends BUDGET_EXCEEDED with partial results); the
notifications are written to the outbox in the same transaction.

**Deciding** (``approve`` and ``decline``): only an authenticated human with an approver role, of
the run's tenant (from the token), with a step-up claim younger than the configured age, may
decide; the decision binds the request id, the run, the tenant and the amount; it is single use
(a unique index on the request's decision: approve, decline and timeout race and exactly one
wins); an expired request, a request of an older cap version, a replay, an amount not above the
current cap, below the spend made or above the ceiling are all refused with ``ApprovalInvalid``.
An approval **appends** a new cap version (never edits one) and writes the enforcement columns
through ``spend.repo`` (the only writer). If the new cap needs more tenant credits (only when a
credit rate is configured, D-04) it goes through ``WalletPort`` as a conditional reserve; a
refusal ends in the decline path (M11). ``expire`` records the timeout; a late approval after it
is refused and recorded.

Run state (AWAITING_APPROVAL with ``awaiting_kind = 'cap'`` and back) is moved by the workflow
through the state machine function; this module never touches ``account_runs.status``.

GUARANTEE(test_pause_request_is_computed_in_code_with_bases): M4.
GUARANTEE(test_one_pause_request_per_cap_version_under_races): M3.
GUARANTEE(test_every_bad_approval_is_refused): M5 lying scenarios.
GUARANTEE(test_approval_appends_a_version_and_raises_the_hard_limit): M5.
GUARANTEE(test_decline_and_timeout_stop_paid_work_and_a_late_approval_is_refused): D-45.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spend import notify, repo
from spend.config import SpendConfig, V2Config
from spend.errors import ApprovalInvalid, NotConfigured
from spend.money import round_up_to_step

CAP_GRANULE_MICROS = 100  # account_runs.budget_usd keeps 4 decimals (spend.service)


@dataclass(frozen=True)
class BlockedUnit:
    """A unit the cap stopped, with its upper-bound estimate (None: unknown) and its rows."""

    unit_key: str
    estimate_micros: int | None
    rows: tuple[str, ...]


@dataclass(frozen=True)
class PauseRequest:
    """What an approver is shown (micro-dollars; every number with its base)."""

    cap_version: int
    current_cap_micros: int
    committed_micros: int
    held_micros: int
    blocked_known_micros: int
    blocked_unknown_units: int
    unknown_hold_micros: int
    needed_micros: int
    shortfall_micros: int
    suggested_cap_micros: int
    capped_by_ceiling: bool
    room: bool
    rows_at_risk: tuple[str, ...]
    breakdown: tuple[dict[str, Any], ...] = ()
    bases: dict[str, str] = field(default_factory=dict)

    def content_hash(self) -> str:
        """sha256 of the canonical request (bound by the approval)."""
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode()).hexdigest()


def compute_request(
    cap_version: int,
    cap_micros: int,
    committed_micros: int,
    held_micros: int,
    blocked: Sequence[BlockedUnit],
    step_micros: int,
    unknown_hold_micros: int,
    ceiling_micros: int | None,
    breakdown: Sequence[dict[str, Any]] = (),
) -> PauseRequest:
    """The pause request (see the module docstring). Pure."""
    known = sum(b.estimate_micros or 0 for b in blocked)
    unknown = sum(1 for b in blocked if b.estimate_micros is None)
    needed = committed_micros + held_micros + known + unknown * unknown_hold_micros
    shortfall = max(0, needed - cap_micros)
    suggested = round_up_to_step(max(needed, cap_micros + step_micros), step_micros)
    capped = False
    if ceiling_micros is not None and suggested > ceiling_micros:
        suggested, capped = ceiling_micros, True
    rows = tuple(sorted({r for b in blocked for r in b.rows}))
    return PauseRequest(
        cap_version=cap_version,
        current_cap_micros=cap_micros,
        committed_micros=committed_micros,
        held_micros=held_micros,
        blocked_known_micros=known,
        blocked_unknown_units=unknown,
        unknown_hold_micros=unknown_hold_micros,
        needed_micros=needed,
        shortfall_micros=shortfall,
        suggested_cap_micros=suggested,
        capped_by_ceiling=capped,
        room=suggested > cap_micros,
        rows_at_risk=rows,
        breakdown=tuple(breakdown),
        bases={
            "committed_micros": "settled calls in the insert-only ledger",
            "held_micros": "reservations of calls in flight",
            "blocked_known_micros": f"upper-bound estimates of {len(blocked) - unknown} units",
            "unknown_hold_micros": f"configured sample hold for each of {unknown} units with no "
            "cost estimate (an estimate, never zero)",
            "needed_micros": "committed + held + blocked estimates",
            "shortfall_micros": "needed minus the current cap (never negative)",
            "suggested_cap_micros": "needed rounded up to the configured step, at least one step "
            "above the current cap, at most the tenant ceiling",
        },
    )


@dataclass(frozen=True)
class Approver:
    """The person deciding, as the verified token states it."""

    subject: str
    tenant_id: str
    role: str
    kind: str = "human"
    step_up_at: datetime | None = None


class WalletPort(Protocol):
    """Tenant credits for an extension (conditional reserve; refused when not enough)."""

    async def reserve_extra(self, tenant_id: UUID, run_id: UUID, credits: Decimal) -> bool:
        """Reserve more credits for the run; False when the balance cannot cover them."""
        ...


class UnconfiguredWallet:
    """Production placeholder until the wallet extension adapter exists (never a fake success)."""

    async def reserve_extra(self, tenant_id: UUID, run_id: UUID, credits: Decimal) -> bool:
        """Always ``NotConfigured``."""
        raise NotConfigured("wallet extension reserve")


async def _pauses(s: AsyncSession, run_id: UUID) -> list[Any]:
    rows = (
        await s.execute(
            text(
                "SELECT p.request_id, p.cap_version, p.at, p.detail_json,"
                " (SELECT d.event FROM spend_limit_events d WHERE d.request_id = p.request_id"
                "   AND d.event IN ('approved', 'declined', 'timed_out')) AS decision"
                " FROM spend_limit_events p WHERE p.run_id = :r AND p.event = 'pause_opened'"
                " ORDER BY p.id"
            ),
            {"r": run_id},
        )
    ).all()
    return list(rows)


async def open_pause(
    s: AsyncSession, run_id: UUID, request: PauseRequest, now: datetime, cfg: V2Config
) -> tuple[UUID, bool]:
    """Open (or return) the pause request of the current cap version. Returns ``(request_id,
    open)``: ``open`` is False when policy declined it at once (pause count or no room)."""
    pauses = await _pauses(s, run_id)
    for p in pauses:
        if p.cap_version == request.cap_version:
            return UUID(str(p.request_id)), p.decision is None
    request_id = uuid.uuid4()
    detail = {**asdict(request), "request_hash": request.content_hash()}
    try:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO spend_limit_events (run_id, cap_version, event, request_id,"
                    " detail_json, at) VALUES (:r, :v, 'pause_opened', :q, CAST(:d AS jsonb), :at)"
                ),
                {
                    "r": run_id,
                    "v": request.cap_version,
                    "q": request_id,
                    "d": json.dumps(detail, sort_keys=True, default=str),
                    "at": now,
                },
            )
    except IntegrityError:  # a racing opener won: return its request (one per cap version)
        for p in await _pauses(s, run_id):
            if p.cap_version == request.cap_version:
                return UUID(str(p.request_id)), p.decision is None
        raise  # pragma: no cover - the unique index guarantees the row exists
    policy = None
    if len(pauses) >= cfg.max_pauses_per_run:
        policy = "max_pauses_reached"
    elif not request.room:
        policy = "ceiling_reached"
    if policy is not None:
        await _decision(
            s,
            run_id,
            request.cap_version,
            request_id,
            "declined",
            "policy",
            now,
            {"reason": policy},
        )
        return request_id, False
    params = {"run_id": str(run_id), "request_id": str(request_id)}
    await notify.enqueue(
        s,
        run_id,
        f"pause:{request_id}",
        "operator",
        "spend_pause_opened",
        {**params, "cap_version": request.cap_version},
        now,
    )
    await notify.enqueue(
        s, run_id, f"pause-tenant:{request_id}", "tenant", "run_waiting_for_approval", params, now
    )
    return request_id, True


async def _decision(
    s: AsyncSession,
    run_id: UUID,
    cap_version: int,
    request_id: UUID,
    event: str,
    principal: str,
    now: datetime,
    detail: dict[str, Any],
) -> None:
    try:
        async with s.begin_nested():
            await s.execute(
                text(
                    "INSERT INTO spend_limit_events (run_id, cap_version, event, request_id,"
                    " detail_json, principal, at)"
                    " VALUES (:r, :v, :e, :q, CAST(:d AS jsonb), :p, :at)"
                ),
                {
                    "r": run_id,
                    "v": cap_version,
                    "e": event,
                    "q": request_id,
                    "d": json.dumps(detail, sort_keys=True, default=str),
                    "p": principal[:200],
                    "at": now,
                },
            )
    except IntegrityError as exc:
        raise ApprovalInvalid("this pause request was already decided") from exc


def _check_approver(approver: Approver, run_tenant: str, now: datetime, cfg: V2Config) -> None:
    if approver.kind != "human":
        raise ApprovalInvalid("only a person may decide a pause")
    if approver.tenant_id != run_tenant:
        raise ApprovalInvalid("the approver belongs to another tenant")
    if approver.role not in cfg.approver_roles:
        raise ApprovalInvalid("this role may not decide a pause")
    if approver.step_up_at is None or now - approver.step_up_at > timedelta(
        minutes=cfg.step_up_max_age_minutes
    ):
        raise ApprovalInvalid("a fresh step-up is required")


async def _open_request(
    s: AsyncSession, run_id: UUID, request_id: UUID, now: datetime, cfg: V2Config
) -> tuple[Any, Any]:
    """The run's money row and the request, both checked (``ApprovalInvalid`` otherwise)."""
    run = await repo.run_money(s, run_id)
    if run is None:
        raise ApprovalInvalid("run not found for this tenant")
    pauses = await _pauses(s, run_id)
    match = next((p for p in pauses if str(p.request_id) == str(request_id)), None)
    if match is None:
        raise ApprovalInvalid("no such pause request for this run")
    if match.decision == "timed_out":
        await s.execute(
            text(
                "INSERT INTO spend_limit_events (run_id, cap_version, event, request_id, at)"
                " VALUES (:r, :v, 'late_approval_refused', :q, :at)"
            ),
            {"r": run_id, "v": match.cap_version, "q": request_id, "at": now},
        )
        raise ApprovalInvalid("the pause request timed out")
    if match.decision is not None:
        raise ApprovalInvalid("this pause request was already decided")
    if match.cap_version != run.cap_version:
        raise ApprovalInvalid("the pause request belongs to an older cap version")
    if now - match.at > timedelta(hours=cfg.pause_timeout_hours):
        raise ApprovalInvalid("the pause request expired")
    return run, match


async def approve(
    s: AsyncSession,
    run_id: UUID,
    request_id: UUID,
    approver: Approver,
    amount_micros: int | None,
    now: datetime,
    cfg: V2Config,
    spend_cfg: SpendConfig,
    wallet: WalletPort,
) -> int:
    """Approve (the suggested or a custom amount); returns the new cap. One transaction."""
    run, match = await _open_request(s, run_id, request_id, now, cfg)
    _check_approver(approver, str(run.tenant_id), now, cfg)
    detail = dict(match.detail_json)
    suggested = int(detail["suggested_cap_micros"])
    amount = suggested if amount_micros is None else amount_micros
    current = int(run.budget_micros or 0)
    if amount % CAP_GRANULE_MICROS:
        raise ApprovalInvalid("the amount must be a whole number of hundredths of a cent")
    if amount <= current:
        raise ApprovalInvalid("the new cap must be above the current cap")
    if amount < run.spent_micros:
        raise ApprovalInvalid("the new cap cannot be below the spend already made")
    if cfg.tenant_ceiling_micros is not None and amount > cfg.tenant_ceiling_micros:
        raise ApprovalInvalid("the amount is above the tenant ceiling")
    wallet_note = "not_applicable: no credit rate is configured (D-04 open)"
    if spend_cfg.credit_usd_rates:
        rate = min(spend_cfg.credit_usd_rates.values())  # the conservative (cheapest) rate
        extra = (Decimal(amount - current) / Decimal(1_000_000) / rate).quantize(Decimal("1"))
        if not await wallet.reserve_extra(UUID(str(run.tenant_id)), run_id, extra):
            await _decision(
                s,
                run_id,
                match.cap_version,
                request_id,
                "declined",
                "wallet",
                now,
                {"reason": "wallet_refused"},
            )
            return current
        wallet_note = f"reserved {extra} credits"
    who = f"human:{approver.subject}"
    await _decision(
        s,
        run_id,
        match.cap_version,
        request_id,
        "approved",
        who,
        now,
        {
            "amount_micros": amount,
            "suggested_micros": suggested,
            "wallet": wallet_note,
            "request_hash": detail.get("request_hash"),
        },
    )
    version = await repo.latest_version(s, run_id) + 1
    body = {
        "extension": True,
        "pause_request": str(request_id),
        "base_version": run.cap_version,
        "cap_micros": amount,
        "snapshot_taken_at": [],
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())
    await repo.insert_version(
        s,
        run_id,
        version,
        amount,
        body,
        digest.hexdigest(),
        spend_cfg.config_hash,
        "extension_requested",
        who,
        "pause approval",
    )
    await repo.add_event(s, run_id, version, "approved", who)
    await repo.add_event(
        s, run_id, int(match.cap_version), "superseded", None, f"by version {version}"
    )
    ops: Any = (
        await s.execute(
            text("SELECT cap_sample_ops FROM account_runs WHERE id = :r"), {"r": run_id}
        )
    ).scalar_one()
    await repo.apply_cap(s, run_id, version, amount, dict(ops or {}))
    await notify.enqueue(
        s,
        run_id,
        f"decided:{request_id}",
        "operator",
        "spend_pause_decided",
        {"run_id": str(run_id), "request_id": str(request_id), "decision": "approved"},
        now,
    )
    return amount


async def decline(
    s: AsyncSession,
    run_id: UUID,
    request_id: UUID,
    approver: Approver,
    now: datetime,
    cfg: V2Config,
) -> None:
    """Decline: the run continues with what it has and ends BUDGET_EXCEEDED (partial results)."""
    run, match = await _open_request(s, run_id, request_id, now, cfg)
    _check_approver(approver, str(run.tenant_id), now, cfg)
    await _decision(
        s, run_id, match.cap_version, request_id, "declined", f"human:{approver.subject}", now, {}
    )
    await notify.enqueue(
        s,
        run_id,
        f"decided:{request_id}",
        "operator",
        "spend_pause_decided",
        {"run_id": str(run_id), "request_id": str(request_id), "decision": "declined"},
        now,
    )


async def expire(s: AsyncSession, run_id: UUID, now: datetime, cfg: V2Config) -> UUID | None:
    """Record the timeout of an open request past its wait (the decline path follows). Returns
    the request it timed out, or None."""
    for p in await _pauses(s, run_id):
        if p.decision is None and now - p.at > timedelta(hours=cfg.pause_timeout_hours):
            try:
                await _decision(
                    s,
                    run_id,
                    int(p.cap_version),
                    UUID(str(p.request_id)),
                    "timed_out",
                    "timeout",
                    now,
                    {},
                )
            except ApprovalInvalid:
                return None  # a racing decision won
            return UUID(str(p.request_id))
    return None

