"""Run lifecycle: the only code that creates runs, moves them between states and settles them.

``account_runs`` is the orchestration run (state machine, money, ledger). ``runs`` is the
data-layer row with the SAME id (tracker and source foreign keys). Both are written together on
the caller's tenant transaction (ADR-0016).

Guarantees (tests: ``tests/runs/test_lifecycle_db.py``):

* ``create_run`` does everything in one transaction: account upsert, both run rows, the credit
  reservation on the client's monthly pool (``replica.credits``, the one credit truth: a run
  either carries out a job confirmed earlier or reserves its own job here; the pool can never go
  below zero), and the seeded tracker. A run without a tracker or without a reservation cannot
  exist. The run's budget is its reserved credits in dollars (never above the configured run
  budget), so a run stops when it reaches its credits. Reusing an idempotency key with a
  different body raises ``IdempotencyConflict`` (known defect 5); the same body returns the
  first run.
* every transition is ``UPDATE ... WHERE status = :expected`` validated against the pure table in
  ``workflow.transitions``; losing a race raises ``RunStateConflict`` and changes nothing.
* ``finish`` sweeps unset rows at any non-COMPLETED end (T12), moves the run through
  ``tracker.guard.finish_run`` (never COMPLETED with gaps), then settles exactly once and stores
  the spend report once (credit cap K12), all in the caller's transaction.
* ``settle_once`` settles the run's job once (``replica.credits.settle_run``): credits used from
  the real spend, never more than reserved, the rest released; ``reserved = committed +
  refunded``; a replay is a no-op. The wallet tables are read-only history (migration 0057).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.runs.transitions import TERMINAL, RunStatus, transition
from account_intelligence.tracker.guard import finish_run
from account_intelligence.tracker.model import ReasonCode
from account_intelligence.tracker.service import TrackerService
from datamodel import pins

SWEEP_REASON = {
    RunStatus.PARTIAL: ReasonCode.SWEPT_UNREPORTED,
    RunStatus.COMPLETED: ReasonCode.SWEPT_UNREPORTED,
    RunStatus.FAILED: ReasonCode.SWEPT_FAILED,
    RunStatus.CANCELED: ReasonCode.SWEPT_CANCELED,
    RunStatus.BUDGET_EXCEEDED: ReasonCode.SWEPT_BUDGET,
}
ZERO_BUDGET_USD = Decimal("0.0001")
# Actors that are not a person: their accounts count against nobody's cap.
SYSTEM_ACTORS = frozenset({"api", "scheduler", "system"})
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
    """The client's monthly credit pool cannot cover the reservation."""


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
    # Unused since the single credit ledger (0057): a run's reservation is its job's credits.
    # Kept so RUN_RESERVE_CREDITS and positional construction keep working.
    reserved_credits: Decimal
    # False only on the local stack with DEV_UNCAPPED=1: no credit-cap gate, the budget above holds
    cap_required: bool = True


@dataclass(frozen=True)
class CreditJob:
    """What the run's credits are for. ``job_id`` names a job confirmed (and reserved) earlier;
    without it the run reserves its own job of ``kind`` for ``credits`` (the rate table's
    estimate when None) on behalf of ``actor``."""

    kind: str = "new_research"
    scope: str = "full"
    actor: str = "api"
    credits: int | None = None
    job_id: UUID | None = None


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
    credit: CreditJob | None = None,
) -> CreatedRun:
    """Create both run rows, reserve credits and seed the tracker in the caller's transaction."""
    from account_intelligence.replica import credits as CR

    file_mode = request.run_input.get("input_mode") == "file"
    credit = credit or (CreditJob(kind="file_analysis", scope="file") if file_mode else CreditJob())
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
    person = None if credit.actor in SYSTEM_ACTORS else credit.actor
    account, _ = await add_account(session, tenant_id, request.domain, added_by=person)
    now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
    if credit.job_id is not None:
        job = (
            await session.execute(
                text("SELECT credits_estimated, status FROM research_jobs WHERE id = :j"),
                {"j": credit.job_id},
            )
        ).first()
        if job is None or job.status not in ("queued", "running"):
            raise InsufficientCredits("the job has no credits reserved")
        credits = int(job.credits_estimated)
        job_id = credit.job_id
    else:
        rate_key = "file_public" if credit.kind == "file_analysis" else credit.kind
        credits = (
            credit.credits
            if credit.credits is not None
            else (await CR.estimate(session, account, rate_key)).credits
        )
        try:
            job_id, _ = await CR.reserve(
                session,
                account_id=account,
                kind=credit.kind,
                scope=credit.scope,
                credits=credits,
                actor=credit.actor,
                now=now,
                one_open_per_account=False,
            )
        except CR.CreditsShort:
            raise InsufficientCredits("insufficient credits") from None
    # The run's budget is its credits in dollars (never above the configured run budget); a run
    # that may spend nothing (reading the client's own files) keeps the smallest positive budget
    # the column holds, so no paid call fits.
    budget_usd = max(
        ZERO_BUDGET_USD, min(limits.budget_usd, Decimal(credits) * CR.rates().credit_value_usd)
    )
    reserved = Decimal(credits)
    run_id = uuid4()
    inserted = (
        await session.execute(
            text(
                "INSERT INTO account_runs (id, tenant_id, account_id, graph_version, budget_json,"
                " reserved_credits, idempotency_key, request_hash, budget_usd, tool_call_cap,"
                " run_input, checklist_version, model_versions, cap_required, input_mode)"
                " VALUES (:id, :t, :a, :gv, CAST(:budget AS jsonb), :credits, :k, :h, :usd, :cap,"
                " CAST(:input AS jsonb), :cl, CAST(:mv AS jsonb), :capreq, :mode)"
                " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id"
            ),
            {
                "id": run_id,
                "t": tenant_id,
                "a": account,
                "gv": limits.graph_version,
                "budget": json.dumps({"usd": str(budget_usd), "depth": request.depth}),
                "credits": reserved,
                "k": request.idempotency_key,
                "h": digest,
                "usd": budget_usd,
                "cap": limits.tool_call_cap,
                "input": json.dumps(request.run_input, sort_keys=True),
                "cl": pins.checklist_version(),
                "mv": json.dumps(pins.model_versions(), sort_keys=True),
                # the credits confirmed on the pool are the run's cap: no second approval, and
                # reaching them stops paid work (BUDGET_EXCEEDED with what was found)
                "capreq": False,
                # input mode "file" researches uploaded documents; anything else is "company"
                "mode": "file" if request.run_input.get("input_mode") == "file" else "company",
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
            "cap": max(1, math.ceil(reserved)),
        },
    )
    await CR.start_with_run(session, job_id, run_id, now)
    await tracker.seed(run_id, tenant_id)
    return CreatedRun(run_id, RunStatus.QUEUED.value, False)


async def record_hermetic_run(
    session: AsyncSession,
    tenant_id: UUID,
    account_id: UUID,
    *,
    reserved_credits: int,
    idempotency_key: str,
    run_input: dict[str, Any],
    grading_config_hash: str,
    now: datetime,
) -> UUID:
    """Record an already finished run with no findings (development and tests only: the hermetic
    research engine). It touches no wallet; the research job's own credits pay for it."""
    run_id: Any = (
        await session.execute(
            text(
                "INSERT INTO account_runs (tenant_id, account_id, status, grading_config_hash,"
                " created_at, finished_at, finished_reason, input_mode, run_mode, cap_required,"
                " graph_version, budget_json, reserved_credits, idempotency_key, run_input)"
                " VALUES (:t, :a, 'COMPLETED', :g, :now, :now, 'completed', 'company', 'full',"
                " false, 'hermetic-research', '{}', :c, :k, CAST(:inp AS jsonb)) RETURNING id"
            ),
            {
                "t": tenant_id,
                "a": account_id,
                "g": grading_config_hash,
                "now": now,
                "c": reserved_credits,
                "k": idempotency_key,
                "inp": json.dumps(run_input),
            },
        )
    ).scalar_one()
    return UUID(str(run_id))


async def record_library_run(
    session: AsyncSession,
    tenant_id: UUID,
    account_id: UUID,
    *,
    library_account_id: UUID,
    now: datetime,
) -> UUID:
    """Record a 6th Library copy as a finished run that used nothing: the copied files and
    narrative hang off it, as any research's do. It touches no credits (the library add is a job
    that used none) and makes no call."""
    run_id: Any = (
        await session.execute(
            text(
                "INSERT INTO account_runs (tenant_id, account_id, status, grading_config_hash,"
                " created_at, finished_at, finished_reason, input_mode, run_mode, cap_required,"
                " graph_version, budget_json, reserved_credits, idempotency_key, run_input)"
                " VALUES (:t, :a, 'COMPLETED', :g, :now, :now, 'library_copy', 'company', 'full',"
                " false, 'library-copy', '{}', 0, :k, CAST(:inp AS jsonb)) RETURNING id"
            ),
            {
                "t": tenant_id,
                "a": account_id,
                "g": "0" * 64,
                "now": now,
                "k": f"library-{account_id}",
                "inp": json.dumps({"library_account_id": str(library_account_id)}),
            },
        )
    ).scalar_one()
    return UUID(str(run_id))


async def mark_library_account(
    session: AsyncSession, account_id: UUID, library_account_id: UUID
) -> None:
    """Record that a new account came from the 6th Library."""
    await session.execute(
        text(
            "UPDATE accounts SET origin = 'library', library_account_id = :l, updated_at = now()"
            " WHERE id = :a"
        ),
        {"a": account_id, "l": library_account_id},
    )


async def name_account(session: AsyncSession, account_id: UUID, name: str) -> None:
    """Give an account its company name when it has none (a typed name never overwrites)."""
    await session.execute(
        text("UPDATE accounts SET name = :n, updated_at = now() WHERE id = :a AND name IS NULL"),
        {"a": account_id, "n": name.strip()[:200]},
    )


async def add_account(
    session: AsyncSession, tenant_id: UUID, domain: str, *, added_by: str | None = None
) -> tuple[UUID, bool]:
    """Add an account to the tenant's list without starting a run (the client app's "add an
    account"); returns (account id, created). A new account added by a person counts against
    that person's account cap (``replica.settings.ensure_account_room``, ``AccountCapReached``)
    whichever way it is added: research, a file, the library. The one writer of ``accounts``
    besides ``create_run`` (W1)."""
    existing: Any = (
        await session.execute(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    ).scalar_one_or_none()
    if existing is not None:
        return UUID(str(existing)), False
    if added_by is not None:
        from account_intelligence.replica.settings import ensure_account_room

        await ensure_account_room(session, added_by)
    created = (
        await session.execute(
            text(
                "INSERT INTO accounts (tenant_id, domain, added_by) VALUES (:t, :d, :by)"
                " ON CONFLICT (tenant_id, domain) DO NOTHING RETURNING id"
            ),
            {"t": tenant_id, "d": domain, "by": added_by},
        )
    ).scalar_one_or_none()
    if created is not None:
        return UUID(str(created)), True
    raced: Any = (
        await session.execute(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    ).scalar_one()
    return UUID(str(raced)), False


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
    # every legal non-terminal target is active (no transition leads back to QUEUED)
    await session.execute(
        text(
            "UPDATE runs SET status = :s, started_at = COALESCE(started_at, now())"
            " WHERE id = :r AND status <> :s"
        ),
        {"s": _RUNS_ACTIVE[new], "r": run_id},
    )


async def finish(
    session: AsyncSession,
    tracker: TrackerService,
    run_id: UUID,
    target: RunStatus,
    *,
    reason: str | None = None,
    refund_all: bool = False,
    detail: str | None = None,
) -> tuple[str, Settlement]:
    """End the run: sweep (non-COMPLETED), guarded terminal update, then the one settlement.
    ``detail`` is the redacted stop description (``runs.stop.describe``), stored once."""
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
    result = await finish_run(
        session, tracker, run_id, expected.value, final.value, reason=reason, detail=detail
    )
    settlement = await settle_once(session, run_id, refund_all=refund_all)
    await close_spend(session, run_id)
    return result.status, settlement


_TO_VERIFYING: dict[RunStatus, RunStatus] = {
    RunStatus.QUEUED: RunStatus.RUNNING,
    RunStatus.AWAITING_APPROVAL: RunStatus.RUNNING,
    RunStatus.RUNNING: RunStatus.DISTILLING,
    RunStatus.DISTILLING: RunStatus.VERIFYING,
}


async def end_from_active(
    session: AsyncSession,
    tracker: TrackerService,
    run_id: UUID,
    target: RunStatus,
    *,
    reason: str,
    detail: str | None = None,
    refund_all: bool = False,
) -> str:
    """End an active run PARTIAL or FAILED from whatever state it is in. PARTIAL is reachable
    only from VERIFYING (every non-failed end passes DISTILLING and VERIFYING), so the run is
    walked there first; FAILED is legal from every active state. A terminal run is left as it is
    (its status is returned)."""
    if target not in (RunStatus.PARTIAL, RunStatus.FAILED):
        raise LifecycleError("end_from_active ends a run PARTIAL or FAILED only")
    current = await current_status(session, run_id)
    if current in TERMINAL:
        return current.value
    if target is RunStatus.PARTIAL:
        # LOOP(walk to verifying): cap=3 hops (QUEUED/AWAITING -> RUNNING -> DISTILLING ->
        # VERIFYING); exit=VERIFYING reached
        while current is not RunStatus.VERIFYING:
            following = _TO_VERIFYING[current]
            await move(session, run_id, current, following)
            current = following
    status, _ = await finish(
        session, tracker, run_id, target, reason=reason, refund_all=refund_all, detail=detail
    )
    return status


async def note_interruption(session: AsyncSession, run_id: UUID, detail: str) -> None:
    """Record why an active run stopped without ending it (a cancelled task, SIGTERM, SIGINT):
    the run stays resumable from its checkpoint and the note says what happened last."""
    await session.execute(
        text(
            "UPDATE account_runs SET stop_detail = :d WHERE id = :r"
            " AND status NOT IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED', 'FAILED', 'CANCELED')"
        ),
        {"d": detail[:600], "r": run_id},
    )


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


async def settle_once(
    session: AsyncSession, run_id: UUID, *, refund_all: bool = False
) -> Settlement:
    """Settle the run's job exactly once (``replica.credits.settle_run``): credits used from the
    real spend (paid data calls and model calls), never more than reserved, the rest released.
    A FAILED run (platform fault) or ``refund_all`` charges nothing. The person who asked is
    told the job ended. A run from before the single ledger (no job) settles nothing."""
    from account_intelligence.replica import credits as CR
    from account_intelligence.replica.notifications import notify

    run = (
        await session.execute(
            text(
                "SELECT tenant_id, status, reserved_credits, spent_usd"
                " FROM account_runs WHERE id = :r FOR UPDATE"
            ),
            {"r": run_id},
        )
    ).first()
    if run is None:
        raise RunNotFound(str(run_id))
    if RunStatus(run.status) not in TERMINAL:
        raise LifecycleError("only a terminal run is settled")
    now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
    platform_fault = refund_all or RunStatus(run.status) is RunStatus.FAILED
    # real spend: the paid data calls (spent_usd) and the model calls of this run (llm_calls)
    model_usd = await session.scalar(
        text("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE run_id = :r"), {"r": run_id}
    )
    settled = await CR.settle_run(
        session,
        run_id,
        run_status=run.status,
        spent_usd=Decimal(run.spent_usd or 0) + Decimal(model_usd or 0),
        platform_fault=platform_fault,
        now=now,
    )
    if settled is None:
        reserved = Decimal(run.reserved_credits)
        return Settlement(reserved, Decimal(0), reserved, True)
    await _usage_settled(session, run_id)
    if not settled.replayed and settled.requested_by not in ("scheduler", "api"):
        await notify(
            session,
            user_id=settled.requested_by,
            kind=_NOTE_KIND.get(settled.kind, "research_finished")
            if settled.status == "done"
            else "research_failed",
            account_id=settled.account_id,
            job_id=settled.job_id,
        )
    return Settlement(
        Decimal(settled.reserved),
        Decimal(settled.used),
        Decimal(settled.released),
        settled.replayed,
    )


_NOTE_KIND = {
    "web_refresh": "refresh_finished",
    "scheduled_refresh": "refresh_finished",
    "new_research": "research_finished",
    "file_analysis": "research_finished",
}


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
