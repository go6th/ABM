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

from account_intelligence.dev_limits import UNCAPPED, UNCAPPED_BUDGET_USD, research_uncapped
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


async def _incremental_keys(
    session: AsyncSession, account: UUID, request: RunRequest, now: datetime
) -> dict[str, str]:
    """The run-input keys of an incremental re-research (``jobs.incremental``): the account's
    previous research to carry and the delta bound of the job feed, or nothing when there is no
    safe base (then the whole window is pulled). A file-only run pulls no job feed. A Phase 2 run
    on a stored pull (``mode`` build) takes the pull's own keys, so both share one feed call."""
    from jobs import incremental as inc

    run_input = request.run_input
    if run_input.get("input_mode") == "file" or inc.KEY_PREVIOUS_RUN in run_input:
        return {}
    if run_input.get("mode") == "build":
        parent = run_input.get("parent_run_id")
        stored = (
            await session.scalar(
                text("SELECT run_input FROM account_runs WHERE id = :p AND account_id = :a"),
                {"p": UUID(str(parent)), "a": account},
            )
            if parent
            else None
        )
        keys = (inc.KEY_PREVIOUS_RUN, inc.KEY_SINCE)
        return {k: str(stored[k]) for k in keys if isinstance(stored, dict) and k in stored}
    resumed = run_input.get("resume_from")
    if resumed:
        # a continuation keeps the incremental inputs of the run it continues (F12): decided
        # again, a base delivered meanwhile or a new month would move the feed's bound, and the
        # feed call it already made (reused at no cost) would be paid for a second time
        stored = await session.scalar(
            text("SELECT run_input FROM account_runs WHERE id = :p AND account_id = :a"),
            {"p": UUID(str(resumed)), "a": account},
        )
        if isinstance(stored, dict):
            keys = (inc.KEY_PREVIOUS_RUN, inc.KEY_SINCE)
            return {k: str(stored[k]) for k in keys if k in stored}
    previous = await inc.previous_delivered_run(
        session, account, inc.current_matching(run_input.get("lanes_off"))
    )
    return inc.incremental_input(previous, now.date())


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
    # incremental re-research (jobs.incremental): decided here, once, for every way a research
    # starts (the API, a file run's linked research, a refresh job). It is not part of the request
    # hash: the same request repeated is the same run
    run_input = {**request.run_input, **await _incremental_keys(session, account, request, now)}
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
    tool_call_cap = limits.tool_call_cap
    if research_uncapped() and credits > 0:
        # caps off (dev_limits): no budget or call count stops a run that may pay for calls; a
        # run with no credits (only the client's own files) still pays for nothing
        budget_usd, tool_call_cap = UNCAPPED_BUDGET_USD, UNCAPPED
    reserved = Decimal(credits)
    run_id = uuid4()
    models = await _run_models(session, account, request.run_input.get("models"))
    inserted = (
        await session.execute(
            text(
                "INSERT INTO account_runs (id, tenant_id, account_id, graph_version, budget_json,"
                " reserved_credits, idempotency_key, request_hash, budget_usd, tool_call_cap,"
                " run_input, checklist_version, model_versions, cap_required, input_mode, models)"
                " VALUES (:id, :t, :a, :gv, CAST(:budget AS jsonb), :credits, :k, :h, :usd, :cap,"
                " CAST(:input AS jsonb), :cl, CAST(:mv AS jsonb), :capreq, :mode,"
                " CAST(:models AS jsonb))"
                " ON CONFLICT (tenant_id, owner_user_id, idempotency_key) DO NOTHING RETURNING id"
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
                "cap": tool_call_cap,
                "input": json.dumps(run_input, sort_keys=True),
                "cl": pins.checklist_version(),
                "mv": json.dumps(pins.model_versions(), sort_keys=True),
                # the credits confirmed on the pool are the run's cap: no second approval, and
                # reaching them stops paid work (BUDGET_EXCEEDED with what was found)
                "capreq": False,
                # input mode "file" researches uploaded documents; anything else is "company"
                "mode": "file" if request.run_input.get("input_mode") == "file" else "company",
                "models": json.dumps(models),
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


async def record_file_import_run(
    session: AsyncSession, tenant_id: UUID, account_id: UUID, document_id: UUID, *, now: datetime
) -> UUID:
    """The run an uploaded deliverable workbook's rows hang off (``documents.deliverables``,
    2026-10-16): finished the moment it is made, no credits, no call, one per document (a
    second import of the same document reuses it)."""
    key = f"file-import-{document_id}"
    existing: Any = (
        await session.execute(
            text("SELECT id FROM account_runs WHERE account_id = :a AND idempotency_key = :k"),
            {"a": account_id, "k": key},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return UUID(str(existing))
    from sqlalchemy.exc import IntegrityError

    insert = text(
        "INSERT INTO account_runs (tenant_id, account_id, status, grading_config_hash,"
        " created_at, finished_at, finished_reason, input_mode, run_mode, cap_required,"
        " graph_version, budget_json, reserved_credits, idempotency_key, run_input)"
        " VALUES (:t, :a, 'COMPLETED', :g, :now, :now, 'file_import', 'file', 'full',"
        " false, 'file-import', '{}', 0, :k, CAST(:inp AS jsonb)) RETURNING id"
    )
    params = {
        "t": tenant_id,
        "a": account_id,
        "g": "0" * 64,
        "now": now,
        "k": key,
        "inp": json.dumps({"document_id": str(document_id), "mode": "file_import"}),
    }
    try:
        # two sweeps importing one document at the same moment: the second takes the first's run
        # (the unique idempotency key refuses a second row; seen on staging 2026-10-16)
        async with session.begin_nested():
            run_id: Any = (await session.execute(insert, params)).scalar_one()
    except IntegrityError:
        run_id = (
            await session.execute(
                text("SELECT id FROM account_runs WHERE account_id = :a AND idempotency_key = :k"),
                {"a": account_id, "k": key},
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
    account"); returns (account id, created). Accounts are not capped: a user's researches and
    chats are (``replica.limits``, owner decision 2026-10-14). The one writer of ``accounts``
    besides ``create_run`` (W1)."""
    # private workspaces (0063): an account is one user's; the owner is the person adding it,
    # else the acting user, so a system job adding for someone names them
    owner = {"d": domain, "by": added_by}
    mine = "domain = :d AND owner_user_id = coalesce(CAST(:by AS text), app_user())"
    existing: Any = (
        await session.execute(text(f"SELECT id FROM accounts WHERE {mine}"), owner)  # noqa: S608
    ).scalar_one_or_none()
    if existing is not None:
        return UUID(str(existing)), False
    created = (
        await session.execute(
            text(
                "INSERT INTO accounts (tenant_id, domain, added_by, owner_user_id)"
                " VALUES (:t, :d, :by, coalesce(CAST(:by AS text), app_user()))"
                " ON CONFLICT (tenant_id, owner_user_id, domain) DO NOTHING RETURNING id"
            ),
            {"t": tenant_id, **owner},
        )
    ).scalar_one_or_none()
    if created is not None:
        return UUID(str(created)), True
    raced: Any = (
        await session.execute(text(f"SELECT id FROM accounts WHERE {mine}"), owner)  # noqa: S608
    ).scalar_one()
    return UUID(str(raced)), False


async def _run_models(session: AsyncSession, account: UUID, chosen: Any) -> dict[str, Any]:
    """The research and analysis models a new run records (owner decision 2026-10-14): the
    models the request chose (already checked by the route that took them), else the account
    owner's current models."""
    from account_intelligence.replica import models as M
    from llm.choice import RunModels

    if isinstance(chosen, dict) and chosen:
        return RunModels.from_json(chosen).as_json()
    owner = await session.scalar(
        text("SELECT owner_user_id FROM accounts WHERE id = :a"), {"a": account}
    )
    current, _ = await M.current(session, str(owner))
    return current.as_json()


async def record_models(session: AsyncSession, run_id: UUID, models: dict[str, Any]) -> None:
    """Record what a run's models are now (a step fell back to another model, with why)."""
    await session.execute(
        text("UPDATE account_runs SET models = CAST(:m AS jsonb) WHERE id = :r"),
        {"m": json.dumps(models), "r": run_id},
    )


async def current_status(session: AsyncSession, run_id: UUID) -> RunStatus:
    """The run's status (``RunNotFound`` if invisible to this tenant)."""
    status = (
        await session.execute(text("SELECT status FROM account_runs WHERE id = :r"), {"r": run_id})
    ).scalar_one_or_none()
    if status is None:
        raise RunNotFound(str(run_id))
    return RunStatus(status)


async def begin_slice(
    session: AsyncSession, run_id: UUID, *, expected: int | None = None
) -> int | None:
    """Count one more slice for the run (the slice ceiling is checked by the runner). With
    ``expected`` (the slice number being started, which equals ``slices_used`` while the queue
    is in order: a run starts at 0, resumes at ``slices_used`` and continues at ``slice_no +
    1``), a slice that already began is not counted again and None is returned."""
    where = " AND slices_used = :expected" if expected is not None else ""
    params: dict[str, Any] = {"r": run_id}
    if expected is not None:
        params["expected"] = expected
    used = (
        await session.execute(
            text(
                f"UPDATE account_runs SET slices_used = slices_used + 1 WHERE id = :r{where}"
                " RETURNING slices_used"
            ),
            params,
        )
    ).scalar_one_or_none()
    return int(used) if used is not None else None


async def slices_used(session: AsyncSession, run_id: UUID) -> int:
    """How many slices the run has begun (``RunNotFound`` if invisible to this tenant)."""
    used = (
        await session.execute(
            text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": run_id}
        )
    ).scalar_one_or_none()
    if used is None:
        raise RunNotFound(str(run_id))
    return int(used)


async def model_replies(session: AsyncSession, run_id: UUID) -> int:
    """How many model replies the run has stored so far (router ledger plus the research graph's
    reply log): work a slice leaves behind even while its node is still pending, so a long node
    that is paying for answers is not counted as stalled (staging 2026-10-28: the pipelines node
    read postings for three slices and the run was ended no_progress)."""
    return int(
        await session.scalar(
            text(
                "SELECT (SELECT count(*) FROM llm_calls WHERE run_id = :r)"
                " + (SELECT count(*) FROM model_reply_log"
                "    WHERE run_id = :r AND served_from = 'model')"
            ),
            {"r": run_id},
        )
        or 0
    )


async def count_no_progress(session: AsyncSession, run_id: UUID, *, progressed: bool) -> int:
    """Count a slice that ended on its timer: the run of slices that moved nothing grows by one,
    or starts again at zero when this slice moved the checkpoint. Returns the run's length (the
    runner ends the research past ``max_no_progress_slices``; staging 2026-10-26: 43 slices in
    a row with the same node pending, nothing counted them)."""
    count = (
        await session.execute(
            text(
                "UPDATE account_runs SET no_progress_slices ="
                " CASE WHEN :p THEN 0 ELSE no_progress_slices + 1 END"
                " WHERE id = :r RETURNING no_progress_slices"
            ),
            {"r": run_id, "p": progressed},
        )
    ).scalar_one_or_none()
    if count is None:
        raise RunNotFound(str(run_id))
    return int(count)


async def completed_calls(session: AsyncSession, run_id: UUID) -> int:
    """How many provider calls the run has finished (ledger ``tool_calls`` rows, one per call
    that completed): work a slice leaves behind even while its node is still pending, the other
    half of the no-progress test beside ``model_replies`` (orchestration review 2026-10-30, F7:
    a pull that finishes a few provider calls per slice was counted as stalled)."""
    return int(
        await session.scalar(
            text("SELECT count(*) FROM tool_calls WHERE run_id = :r"), {"r": run_id}
        )
        or 0
    )


async def note_pause(session: AsyncSession, run_id: UUID) -> None:
    """The run entered a wait for a person: the slice that resumes it is not research time. The
    count (``run_input.pause_slices``) raises the slice ceiling by one per wait, so approvals,
    spend pauses and token-guard pauses do not eat the slices the research needs (F4)."""
    await session.execute(
        text(
            "UPDATE account_runs SET run_input = jsonb_set(COALESCE(run_input, '{}'::jsonb),"
            " '{pause_slices}', to_jsonb(COALESCE((run_input->>'pause_slices')::int, 0) + 1))"
            " WHERE id = :r"
        ),
        {"r": run_id},
    )


async def pause_slices(session: AsyncSession, run_id: UUID) -> int:
    """How many waits for a person the run has been through (``note_pause``)."""
    return int(
        await session.scalar(
            text(
                "SELECT COALESCE((run_input->>'pause_slices')::int, 0) FROM account_runs"
                " WHERE id = :r"
            ),
            {"r": run_id},
        )
        or 0
    )


async def note_pause_lapsed(session: AsyncSession, run_id: UUID) -> None:
    """A wait for a person ran out of time (``expire_cap_pause``): the slice that resumes the run
    must read the pause as declined, never as approved (F10). The mark is the number of the slice
    that resumes it (``slices_used`` now), so it answers that one wait only and needs no clearing:
    a redelivered copy of the slice reads it again, a later wait has a later slice number."""
    await session.execute(
        text(
            "UPDATE account_runs SET run_input = jsonb_set(COALESCE(run_input, '{}'::jsonb),"
            " '{pause_lapsed}', to_jsonb(slices_used)) WHERE id = :r"
        ),
        {"r": run_id},
    )


async def pause_lapsed(session: AsyncSession, run_id: UUID, slice_no: int) -> bool:
    """Whether the wait that slice ``slice_no`` resumes ran out of time (``note_pause_lapsed``)."""
    marked = await session.scalar(
        text("SELECT (run_input->>'pause_lapsed')::int FROM account_runs WHERE id = :r"),
        {"r": run_id},
    )
    return marked is not None and int(marked) == slice_no


async def pause_declined(session: AsyncSession, run_id: UUID) -> bool:
    """Whether the run's newest spend-pause request was declined or timed out (the person said
    no, or nobody answered): the graph then reads the interrupt as a decline, not an approval."""
    event = await session.scalar(
        text(
            "SELECT d.event FROM spend_limit_events p"
            " JOIN spend_limit_events d ON d.request_id = p.request_id"
            "  AND d.event IN ('approved', 'declined', 'timed_out')"
            " WHERE p.run_id = :r AND p.event = 'pause_opened' ORDER BY p.id DESC LIMIT 1"
        ),
        {"r": run_id},
    )
    return event in ("declined", "timed_out")


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


async def mark_stalled(session: AsyncSession, run_id: UUID) -> Any:
    """Mark a running run stalled once (no progress for the platform's ``stall_after_s``); the
    run's owner and account, or None when it was already marked or has ended."""
    return (
        await session.execute(
            text(
                "UPDATE account_runs SET stalled_at = now() WHERE id = :r AND stalled_at IS NULL"
                " AND status IN ('RUNNING', 'DISTILLING', 'VERIFYING')"
                " RETURNING owner_user_id, account_id"
            ),
            {"r": run_id},
        )
    ).first()


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
    # real spend: the paid data calls (spent_usd) and the model calls of this run: the router's
    # ledger (llm_calls) or the research graph's reply log (model_reply_log, written while the
    # router is off), the larger of the two, as the token meter reads them (audit 2026-10-21: the
    # settlement read llm_calls only, so graph runs settled without their model spend)
    model_usd = await session.scalar(
        text(
            "SELECT GREATEST("
            " (SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE run_id = :r),"
            " (SELECT coalesce(sum(cost_usd), 0) FROM model_reply_log"
            "  WHERE run_id = :r AND served_from = 'model'))"
        ),
        {"r": run_id},
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
    # a token-guard pause has no request to time out, so the lapse is marked on the run: the
    # resumed graph reads every lapsed wait as "no" (F10; it used to read it as "continue")
    await note_pause_lapsed(session, run_id)
    await move(session, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
    return True


async def waiting(s: AsyncSession, run_id: UUID) -> Any:
    """Why the run waits (``awaiting_kind``) and the slices it used, or None when not found."""
    return (
        await s.execute(
            text("SELECT awaiting_kind, slices_used FROM account_runs WHERE id = :r"),
            {"r": run_id},
        )
    ).first()
