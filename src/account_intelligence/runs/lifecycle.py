"""Run lifecycle: the only code that creates runs, moves them between states and settles them.

``account_runs`` is the orchestration run (state machine, money, ledger). ``runs`` is the
data-layer row with the SAME id (tracker and source foreign keys). Both are written together on
the caller's tenant transaction (ADR-0016).

Guarantees (tests: ``tests/runs/test_lifecycle_db.py``):

* ``create_run`` does everything in one transaction: account upsert, both run rows, the credit
  reservation (no negative balance), the reserve ledger entry and the seeded tracker. A run without
  a tracker or without a reservation cannot exist. Reusing an idempotency key with a different body
  raises ``IdempotencyConflict`` (known defect 5); the same body returns the first run.
* every transition is ``UPDATE ... WHERE status = :expected`` validated against the pure table in
  ``workflow.transitions``; losing a race raises ``RunStateConflict`` and changes nothing.
* ``finish`` sweeps unset rows at any non-COMPLETED end (T12), moves the run through
  ``tracker.guard.finish_run`` (never COMPLETED with gaps), then settles exactly once and stores
  the spend report once (credit cap K12), all in the caller's transaction.
* ``settle_once`` writes one commit and one refund entry per run (unique indexes, row lock);
  ``reserved = committed + refunded``; a replay is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.runs.transitions import TERMINAL, RunStatus, transition
from account_intelligence.tracker.guard import finish_run
from account_intelligence.tracker.model import ReasonCode
from account_intelligence.tracker.service import TrackerService

SWEEP_REASON = {
    RunStatus.PARTIAL: ReasonCode.SWEPT_UNREPORTED,
    RunStatus.COMPLETED: ReasonCode.SWEPT_UNREPORTED,
    RunStatus.FAILED: ReasonCode.SWEPT_FAILED,
    RunStatus.CANCELED: ReasonCode.SWEPT_CANCELED,
    RunStatus.BUDGET_EXCEEDED: ReasonCode.SWEPT_BUDGET,
}
_RUNS_ACTIVE = {
    RunStatus.RUNNING: "running",
    RunStatus.DISTILLING: "running",
    RunStatus.VERIFYING: "running",
    RunStatus.AWAITING_APPROVAL: "paused",
}


class LifecycleError(Exception):
    """Base class for run lifecycle errors."""


class IdempotencyConflict(LifecycleError):
    """The idempotency key was used before with a different request body."""


class InsufficientCredits(LifecycleError):
    """The tenant's wallet cannot cover the reservation."""


class RunNotFound(LifecycleError):
    """No such run for this tenant (RLS hides other tenants' runs)."""


class RunStateConflict(LifecycleError):
    """The run is no longer in the expected state (another worker moved it)."""


@dataclass(frozen=True)
class RunRequest:
    """What a client asks for. The tenant is NOT here: it comes from the verified token (W3)."""

    domain: str
    depth: str
    idempotency_key: str
    run_input: dict[str, Any]

    def request_hash(self) -> str:
        """SHA-256 of the canonical body (everything but the key)."""
        body = {"domain": self.domain, "depth": self.depth, "run_input": self.run_input}
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class RunLimits:
    """Per-run caps from config (skill defaults: $5.00 broker cap, 60 tool calls)."""

    graph_version: str
    budget_usd: Decimal
    tool_call_cap: int
    reserved_credits: Decimal


@dataclass(frozen=True)
class CreatedRun:
    """The run's id and status; ``replayed`` when the key was seen before with the same body."""

    run_id: UUID
    status: str
    replayed: bool


@dataclass(frozen=True)
class Settlement:
    """One run's settlement (credits)."""

    reserved: Decimal
    committed: Decimal
    refunded: Decimal
    replayed: bool


async def create_run(
    session: AsyncSession,
    tenant_id: UUID,
    request: RunRequest,
    limits: RunLimits,
    tracker: TrackerService,
) -> CreatedRun:
    """Create both run rows, reserve credits and seed the tracker in the caller's transaction."""
    digest = request.request_hash()
    prior = (
        await session.execute(
            text("SELECT id, status, request_hash FROM account_runs WHERE idempotency_key = :k"),
            {"k": request.idempotency_key},
        )
    ).first()
    if prior is not None:
        if prior.request_hash != digest:
            raise IdempotencyConflict("idempotency key reused with a different request body")
        return CreatedRun(prior.id, prior.status, True)
    account: UUID = (
        await session.execute(
            text(
                "INSERT INTO accounts (tenant_id, domain) VALUES (:t, :d)"
                " ON CONFLICT (tenant_id, domain) DO UPDATE SET domain = EXCLUDED.domain"
                " RETURNING id"
            ),
            {"t": tenant_id, "d": request.domain},
        )
    ).scalar_one()
    run_id = uuid4()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO account_runs (id, tenant_id, account_id, graph_version, budget_json,"
                " reserved_credits, idempotency_key, request_hash, budget_usd, tool_call_cap,"
                " run_input) VALUES (:id, :t, :a, :gv, CAST(:budget AS jsonb), :credits, :k,"
                " :h, :usd, :cap, CAST(:input AS jsonb))"
                " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
            ),
            {
                "id": run_id,
                "t": tenant_id,
                "a": account,
                "gv": limits.graph_version,
                "budget": json.dumps({"usd": str(limits.budget_usd), "depth": request.depth}),
                "credits": limits.reserved_credits,
                "k": request.idempotency_key,
                "h": digest,
                "usd": limits.budget_usd,
                "cap": limits.tool_call_cap,
                "input": json.dumps(request.run_input, sort_keys=True),
            },
        )
    ).first()
    if inserted is None:  # a concurrent request with the same key won the insert
        raise RunStateConflict("a concurrent request created this run; retry to read it")
    await session.execute(
        text(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap) VALUES (:id, :t, :a, 'full', 'queued', :gv, :cap)"
        ),
        {
            "id": run_id,
            "t": tenant_id,
            "a": account,
            "gv": limits.graph_version,
            "cap": max(1, math.ceil(limits.reserved_credits)),
        },
    )
    balance = (
        await session.execute(
            text(
                "UPDATE wallets SET balance = balance - :e WHERE tenant_id = :t AND balance >= :e"
                " RETURNING balance"
            ),
            {"e": limits.reserved_credits, "t": tenant_id},
        )
    ).first()
    if balance is None:
        raise InsufficientCredits("insufficient credits")  # the caller's transaction rolls back
    await session.execute(
        text(
            "INSERT INTO wallet_ledger (tenant_id, run_id, entry, amount, balance_after)"
            " VALUES (:t, :r, 'reserve', :amount, :bal)"
        ),
        {"t": tenant_id, "r": run_id, "amount": -limits.reserved_credits, "bal": balance.balance},
    )
    await tracker.seed(run_id, tenant_id)
    return CreatedRun(run_id, RunStatus.QUEUED.value, False)


async def add_account(session: AsyncSession, tenant_id: UUID, domain: str) -> tuple[UUID, bool]:
    """Add an account to the tenant's list without starting a run (the client app's "add an
    account"); returns (account id, created). The one writer of ``accounts`` besides
    ``create_run`` (W1)."""
    created = (
        await session.execute(
            text(
                "INSERT INTO accounts (tenant_id, domain) VALUES (:t, :d)"
                " ON CONFLICT (tenant_id, domain) DO NOTHING RETURNING id"
            ),
            {"t": tenant_id, "d": domain},
        )
    ).scalar_one_or_none()
    if created is not None:
        return UUID(str(created)), True
    existing: Any = (
        await session.execute(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    ).scalar_one()
    return UUID(str(existing)), False


async def current_status(session: AsyncSession, run_id: UUID) -> RunStatus:
    """The run's status (``RunNotFound`` if invisible to this tenant)."""
    status = (
        await session.execute(text("SELECT status FROM account_runs WHERE id = :r"), {"r": run_id})
    ).scalar_one_or_none()
    if status is None:
        raise RunNotFound(str(run_id))
    return RunStatus(status)


async def begin_slice(session: AsyncSession, run_id: UUID) -> int:
    """Count one more slice for the run (the slice ceiling is checked by the runner)."""
    used: int = (
        await session.execute(
            text(
                "UPDATE account_runs SET slices_used = slices_used + 1 WHERE id = :r"
                " RETURNING slices_used"
            ),
            {"r": run_id},
        )
    ).scalar_one()
    return used


async def move(
    session: AsyncSession,
    run_id: UUID,
    expected: RunStatus,
    new: RunStatus,
    *,
    approval_deadline: datetime | None = None,
    awaiting_kind: str | None = None,
) -> None:
    """A non-terminal transition on both run rows (terminal ends go through ``finish``). Entering
    AWAITING_APPROVAL records why the run waits (``approval`` by default, ``cap`` for a pause
    instead of fail, D-45); leaving it clears the reason."""
    if new in TERMINAL:
        raise LifecycleError("terminal states are reached through finish()")
    transition(expected, new)
    if awaiting_kind not in (None, "approval", "cap"):
        raise LifecycleError(f"unknown awaiting kind {awaiting_kind}")
    kind = (awaiting_kind or "approval") if new is RunStatus.AWAITING_APPROVAL else None
    updated = await session.execute(
        text(
            "UPDATE account_runs SET status = :new,"
            " approval_deadline = COALESCE(:deadline, approval_deadline),"
            " awaiting_kind = :kind"
            " WHERE id = :r AND status = :expected"
        ),
        {
            "new": new.value,
            "r": run_id,
            "expected": expected.value,
            "deadline": approval_deadline,
            "kind": kind,
        },
    )
    if updated.rowcount == 0:  # type: ignore[attr-defined]
        raise RunStateConflict(f"run is no longer {expected.value}")
    target = _RUNS_ACTIVE.get(new)
    if target is not None:
        await session.execute(
            text(
                "UPDATE runs SET status = :s, started_at = COALESCE(started_at, now())"
                " WHERE id = :r AND status <> :s"
            ),
            {"s": target, "r": run_id},
        )


async def finish(
    session: AsyncSession,
    tracker: TrackerService,
    run_id: UUID,
    target: RunStatus,
    *,
    reason: str | None = None,
    refund_all: bool = False,
) -> tuple[str, Settlement]:
    """End the run: sweep (non-COMPLETED), guarded terminal update, then the one settlement."""
    expected = await current_status(session, run_id)
    if expected in TERMINAL:
        settlement = await settle_once(session, run_id, refund_all=refund_all)
        await close_spend(session, run_id)  # a re-delivery stores no second report
        return expected.value, settlement
    transition(expected, target)
    final = target
    if target is RunStatus.COMPLETED and not (await tracker.verdict(run_id)).can_complete:
        final = RunStatus.PARTIAL
    if final is not RunStatus.COMPLETED:
        await tracker.sweep(run_id, (await _tenant(session, run_id)), SWEEP_REASON[final])
    result = await finish_run(session, tracker, run_id, expected.value, final.value, reason=reason)
    settlement = await settle_once(session, run_id, refund_all=refund_all)
    await close_spend(session, run_id)
    return result.status, settlement


async def close_spend(session: AsyncSession, run_id: UUID) -> None:
    """The end-of-run spend report (credit cap K12), in the finishing transaction: the terminal
    state, the one settlement and the one report commit together, on every terminal path
    (COMPLETED, PARTIAL, FAILED, CANCELED, BUDGET_EXCEEDED, an approval timeout). Storing is
    idempotent: the same ledger stores no second version. The clock is the transaction's."""
    from account_intelligence.connectors.settings import load_providers_config
    from spend.config import load_spend_config
    from spend.service import close_in_session

    now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
    ttl = int(load_providers_config().defaults.ledger.lease_ttl_s)
    await close_in_session(session, run_id, load_spend_config(), now, ttl)


async def _tenant(session: AsyncSession, run_id: UUID) -> UUID:
    tenant: UUID = (
        await session.execute(
            text("SELECT tenant_id FROM account_runs WHERE id = :r"), {"r": run_id}
        )
    ).scalar_one()
    return tenant


def _credits(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def settle_once(
    session: AsyncSession, run_id: UUID, *, refund_all: bool = False
) -> Settlement:
    """Commit ``reserved * min(1, spent / budget)`` and refund the rest, exactly once (proposal
    D-04, ADR-0016). A FAILED run (platform fault) or ``refund_all`` refunds everything."""
    run = (
        await session.execute(
            text(
                "SELECT tenant_id, status, reserved_credits, spent_usd, budget_usd"
                " FROM account_runs WHERE id = :r FOR UPDATE"
            ),
            {"r": run_id},
        )
    ).first()
    if run is None:
        raise RunNotFound(str(run_id))
    if RunStatus(run.status) not in TERMINAL:
        raise LifecycleError("only a terminal run is settled")
    reserved = Decimal(run.reserved_credits)
    done = (
        await session.execute(
            text(
                "SELECT entry, amount FROM wallet_ledger WHERE run_id = :r"
                " AND entry IN ('commit', 'refund')"
            ),
            {"r": run_id},
        )
    ).all()
    if done:
        by = {row.entry: Decimal(row.amount) for row in done}
        await _usage_settled(session, run_id)
        return Settlement(
            reserved, by.get("commit", Decimal(0)), by.get("refund", Decimal(0)), True
        )
    used = Decimal(0)
    if run.budget_usd and Decimal(run.budget_usd) > 0:
        used = min(Decimal(1), Decimal(run.spent_usd) / Decimal(run.budget_usd))
    platform_fault = refund_all or RunStatus(run.status) is RunStatus.FAILED
    committed = Decimal(0) if platform_fault else _credits(reserved * used)
    refunded = reserved - committed
    balance: Decimal = (
        await session.execute(
            text(
                "UPDATE wallets SET balance = balance + :refund WHERE tenant_id = :t"
                " RETURNING balance"
            ),
            {"refund": refunded, "t": run.tenant_id},
        )
    ).scalar_one()
    for entry, amount in (("commit", committed), ("refund", refunded)):
        await session.execute(
            text(
                "INSERT INTO wallet_ledger (tenant_id, run_id, entry, amount, balance_after)"
                " VALUES (:t, :r, :e, :a, :b)"
                " ON CONFLICT (run_id, entry) WHERE entry IN ('commit', 'refund') DO NOTHING"
            ),
            {"t": run.tenant_id, "r": run_id, "e": entry, "a": amount, "b": balance},
        )
    await _usage_settled(session, run_id)
    return Settlement(reserved, committed, refunded, False)


async def _usage_settled(session: AsyncSession, run_id: UUID) -> None:
    """The run's one ``run_settled`` usage event, in the settlement's transaction (MT1: the
    idempotency key makes every later settlement call add nothing)."""
    from metering.events import record

    now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
    await record(session, "run_settled", run_id, now)


async def expire_approval(
    session: AsyncSession, tracker: TrackerService, run_id: UUID, now: datetime
) -> bool:
    """Defect 8: past the approval deadline the run is CANCELED and fully refunded (it never
    loops into the same interrupt). Returns True when it expired the run."""
    row = (
        await session.execute(
            text("SELECT status, approval_deadline, awaiting_kind FROM account_runs WHERE id = :r"),
            {"r": run_id},
        )
    ).first()
    if row is None or row.status != RunStatus.AWAITING_APPROVAL.value:
        return False
    if row.approval_deadline is None or row.approval_deadline > now:
        return False
    if row.awaiting_kind == "cap":
        return False  # a pause instead of fail times out through expire_cap_pause (D-45)
    await finish(
        session, tracker, run_id, RunStatus.CANCELED, reason="approval_timeout", refund_all=True
    )
    return True


async def expire_cap_pause(session: AsyncSession, run_id: UUID, now: datetime) -> bool:
    """D-45: past its wait a cap pause is recorded ``timed_out`` (the decline path) and the run
    resumes RUNNING so its next slice distils, builds and ends BUDGET_EXCEEDED with partial
    results and one settlement. A pause never loops into the same interrupt (defect 8). Returns
    True when it resumed the run (the caller enqueues the next slice)."""
    from spend.config import load_v2_config
    from spend.pause import expire

    row = (
        await session.execute(
            text("SELECT status, approval_deadline, awaiting_kind FROM account_runs WHERE id = :r"),
            {"r": run_id},
        )
    ).first()
    if row is None or row.status != RunStatus.AWAITING_APPROVAL.value:
        return False
    if row.awaiting_kind != "cap" or row.approval_deadline is None or row.approval_deadline > now:
        return False
    await expire(session, run_id, now, load_v2_config())
    await move(session, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
    return True
