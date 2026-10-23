"""A research continues itself (end-to-end automation, 2026-10-16).

Before this module a run whose worker died (``worker_lost``) or that made no progress
(``stalled``) was ended with what it held and its owner was told; the research went on only when
a person opened the account and pressed Resume. Nobody watches a run in a multi-user product, so
the scheduled sweep now continues such a run the way the button does (``continue_run`` is the
one path for both): the stopped run is ended PARTIAL (FAILED with a full refund when it holds
nothing usable), a continuation job with ``resume_from`` is reserved for the same requester,
scope, topic and models (every finished pull and model reply of the stopped run is reused at no
cost through the research cache), and the worker is handed the job.

Bounded and honest:

* at most ``MAX_AUTO_RESUMES`` automatic continuations per chain (the runs linked by
  ``resume_from``); after that the research stays stopped for its owner, who was told it stalled
  and keeps the Resume button;
* only for stops a retry can mend (``ELIGIBLE``: the worker was lost, no progress, the model was
  unavailable). A stop the owner must fix first (the model provider refused, no model, a spending
  cap, a repeated paid call) is never retried by the platform: each attempt would stop the same
  way and cost a slice;
* only within ``WINDOW_HOURS`` of the stop (the sweep passes every five minutes; an old stop is
  the owner's to resume) and only for runs a research job started (Workflow v1 runs started
  through the API are left to their owner);
* never while a worker still holds the run's lease (the heartbeat may lag one long call);
* idempotent: the continuation's ``request_id`` is derived from the stopped run's id, so a sweep
  that runs twice, or on two workers, reserves one job;
* visible: the owner is told once per continuation (``research_resumed``), the chain is in
  ``research_jobs.resume_from``, and the audit trail records ``run.auto_resume_research``.

GUARANTEE(test_a_stalled_research_is_continued_by_the_sweep_once)
GUARANTEE(test_automatic_continuations_are_bounded)
GUARANTEE(test_stops_the_owner_must_fix_and_old_stops_are_not_continued)
GUARANTEE(test_a_run_the_worker_lost_is_continued_and_a_live_lease_is_respected)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn, untenanted_session
from account_intelligence.replica import credits as CR
from account_intelligence.replica.notifications import notify
from account_intelligence.runs import lifecycle
from account_intelligence.runs.stop import has_usable_output
from account_intelligence.runs.transitions import RunStatus
from obs.logging import logger

_log = logger(__name__)

# how many times the platform continues one research by itself before it leaves it to its owner
MAX_AUTO_RESUMES = 2
# a stop older than this is the owner's to resume (the sweep passes every five minutes)
WINDOW_HOURS = 24
# the stop reasons a retry can mend; every other reason needs a person first
ELIGIBLE = frozenset({"worker_lost", "stalled", "model_unavailable"})
ACTIVE = ("QUEUED", "RUNNING", "DISTILLING", "VERIFYING", "AWAITING_APPROVAL")
SYSTEM_ACTOR = "system:sweeper"
_NAMESPACE = uuid.UUID("3f2d0b7e-6c1a-4a5e-9d8b-2e7c5a1f0b4d")
_TITLES = {
    "not_found": "Not found",
    "nothing_to_resume": "Nothing to resume",
    "still_running": "Still running",
}


def auto_request_id(run_id: UUID) -> UUID:
    """The one request id of the platform's continuation of ``run_id`` (idempotency)."""
    return uuid.uuid5(_NAMESPACE, f"auto-resume:{run_id}")


class NothingToContinue(Exception):
    """The run cannot be continued: ``code`` says why, ``text`` is for the person."""

    def __init__(self, code: str, text_: str) -> None:
        super().__init__(code)
        self.code = code
        self.title = _TITLES.get(code, "Not continued")
        self.text = text_


@dataclass(frozen=True)
class Continued:
    """The continuation job: new (``created``) or the one an earlier identical request made."""

    job_id: UUID
    status: str
    created: bool
    requested_by: str
    account_id: UUID


def _tracker(s: Any, coverage_owners: Any) -> Any:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
    from account_intelligence.tracker.repo import CoverageRepo
    from account_intelligence.tracker.service import TrackerService

    return TrackerService(
        CoverageRepo(s),
        LedgerCallAdapter(s, load_grading_config().category_by_operation),
        RunInputAdapter(s),
        owners=coverage_owners,
    )


async def lease_live(s: Any, run_id: UUID, now: datetime) -> bool:
    """Whether a worker still holds the run (its lease has not expired)."""
    row = (
        await s.execute(
            text("SELECT 1 FROM run_leases WHERE run_id = :r AND expires_at >= :now"),
            {"r": run_id, "now": now},
        )
    ).first()
    return row is not None


async def automatic_continuations(s: Any, run_id: UUID) -> int:
    """How many times the platform already continued this chain (walking ``resume_from`` back
    from ``run_id``; a continuation the owner asked for does not count)."""
    count, current = 0, run_id
    # LOOP(continuation chain): cap=MAX_AUTO_RESUMES + 8 hops; exit=a run no continuation made
    for _ in range(MAX_AUTO_RESUMES + 8):
        row = (
            await s.execute(
                text(
                    "SELECT resume_from, request_id FROM research_jobs WHERE run_id = :r"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"r": current},
            )
        ).first()
        if row is None or row.resume_from is None:
            break
        previous = UUID(str(row.resume_from))
        if row.request_id is not None and UUID(str(row.request_id)) == auto_request_id(previous):
            count += 1
        current = previous
    return count


async def continue_run(
    s: Any,
    *,
    run_id: UUID,
    actor: str,
    request_id: UUID,
    coverage_owners: Any,
    now: datetime,
    automatic: bool = False,
) -> Continued:
    """Continue one stopped or stalled run in the caller's tenant transaction (see the module
    docstring). ``actor`` is the person pressing Resume; a platform continuation (``automatic``)
    is requested by whoever asked for the stopped run. Raises ``NothingToContinue`` or
    ``credits.JobOpen`` (the account already has a research running)."""
    run = (
        await s.execute(
            text(
                "SELECT id, account_id, status, stalled_at, finished_reason, finished_at"
                " FROM account_runs WHERE id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    if run is None:
        raise NothingToContinue("not_found", "This research was not found.")
    if await CR.has_request(s, request_id):  # the same request again: its job
        prior = (
            await s.execute(
                text(
                    "SELECT id, status, requested_by, account_id FROM research_jobs"
                    " WHERE request_id = :q"
                ),
                {"q": request_id},
            )
        ).one()
        return Continued(
            UUID(str(prior.id)),
            str(prior.status),
            False,
            str(prior.requested_by),
            UUID(str(prior.account_id)),
        )
    if run.status == "COMPLETED":
        raise NothingToContinue("nothing_to_resume", "This research finished whole.")
    if run.status in ACTIVE and run.stalled_at is None:
        raise NothingToContinue(
            "still_running",
            "This research is still running. You will be told when it finishes.",
        )
    job = (
        await s.execute(
            text(
                "SELECT kind, scope, topic, models, requested_by, goal FROM research_jobs"
                " WHERE run_id = :r ORDER BY created_at DESC LIMIT 1"
            ),
            {"r": run_id},
        )
    ).first()
    if automatic:
        if job is None:
            raise NothingToContinue("no_job", "No research job started this run.")
        reason = "stalled" if run.status in ACTIVE else str(run.finished_reason or "")
        if reason not in ELIGIBLE:
            raise NothingToContinue("not_automatic", f"A stop of kind {reason} needs a person.")
        if run.status not in ACTIVE and (
            run.finished_at is None or run.finished_at < now - timedelta(hours=WINDOW_HOURS)
        ):
            raise NothingToContinue("too_old", "The stop is older than the platform retries.")
        if await lease_live(s, run_id, now):
            raise NothingToContinue("worker_alive", "A worker still holds this run.")
        if await automatic_continuations(s, run_id) >= MAX_AUTO_RESUMES:
            raise NothingToContinue("limit", "The platform already continued this research.")
    if run.status in ACTIVE:  # stalled: end it with everything it holds, then continue
        usable = await has_usable_output(s, run_id)
        await lifecycle.end_from_active(
            s,
            _tracker(s, coverage_owners),
            run_id,
            RunStatus.PARTIAL if usable else RunStatus.FAILED,
            reason="stalled",
            detail=(
                "stalled: no progress for the platform's stall time; "
                + ("continued by the platform" if automatic else "resumed by its owner")
            ),
            refund_all=not usable,
        )
    kind = str(job.kind) if job is not None else "new_research"
    scope = str(job.scope) if job is not None else "full"
    requester = str(job.requested_by) if automatic and job is not None else actor
    est = await CR.estimate(s, run.account_id, scope if scope in CR.SCOPES else kind)
    job_id, status = await CR.reserve(
        s,
        account_id=run.account_id,
        kind=kind,
        scope=scope,
        credits=est.credits,
        actor=requester,
        now=now,
        request_id=request_id,
        topic=job.topic if job is not None else None,
        models=dict(job.models) if job is not None and job.models else None,
        enforce_pool=False,
        resume_from=run_id,
        goal=job.goal if job is not None else None,
    )
    return Continued(job_id, status, True, requester, UUID(str(run.account_id)))


_CANDIDATES = (
    "SELECT r.id, r.owner_user_id FROM account_runs r"
    " WHERE coalesce(r.input_mode, 'company') = 'company'"
    " AND EXISTS (SELECT 1 FROM research_jobs j WHERE j.run_id = r.id)"
    " AND NOT EXISTS (SELECT 1 FROM research_jobs c WHERE c.resume_from = r.id)"
    " AND ((r.stalled_at IS NOT NULL AND r.status IN ('RUNNING', 'DISTILLING', 'VERIFYING'))"
    "   OR (r.status IN ('PARTIAL', 'FAILED') AND r.finished_reason = ANY(:reasons)"
    "       AND r.finished_at >= :since))"
    " ORDER BY r.created_at LIMIT :n"
)


async def _audit(s: Any, job_id: UUID, now: datetime) -> None:
    from audit.events import record
    from audit.routes_catalog import action_event

    await record(
        s,
        action_event(
            audit_class="run",
            action="auto_resume_research",
            resource_type="research_job",
            resource_id=str(job_id),
            actor=SYSTEM_ACTOR,
            actor_kind="system",
            at=now,
            route=None,
            request_id=None,
        ),
    )


async def auto_resume_pass(
    coverage_owners: Any, now: datetime, *, limit: int = 50
) -> dict[str, int]:
    """One pass over every client (see the module docstring). Returns the counts
    ``continued`` (jobs reserved and handed to the worker) and ``skipped`` (candidates the rules
    left alone this pass)."""
    from account_intelligence.replica import jobs

    async with untenanted_session() as plain:
        tenants = [
            UUID(str(r.tenant_id))
            for r in await plain.execute(text("SELECT tenant_id FROM operator_tenant_overview()"))
        ]
    since = now - timedelta(hours=WINDOW_HOURS)
    continued = skipped = 0
    # LOOP(auto resume clients): cap=every client once per pass, `limit` runs each; exit=last one
    for tenant in tenants:
        async with tenant_txn(tenant, system=True) as s:
            rows = (
                await s.execute(
                    text(_CANDIDATES), {"reasons": sorted(ELIGIBLE), "since": since, "n": limit}
                )
            ).all()
        # LOOP(auto resume runs): cap=`limit` runs per client per pass; exit=rows exhausted
        for row in rows:
            run_id, owner = UUID(str(row.id)), str(row.owner_user_id)
            start: UUID | None = None
            try:
                async with tenant_txn(tenant, user=owner, system=True) as s:
                    try:
                        out = await continue_run(
                            s,
                            run_id=run_id,
                            actor=SYSTEM_ACTOR,
                            request_id=auto_request_id(run_id),
                            coverage_owners=coverage_owners,
                            now=now,
                            automatic=True,
                        )
                    except (NothingToContinue, CR.JobOpen):
                        skipped += 1
                        continue
                    if not out.created:
                        continue
                    if out.requested_by not in ("scheduler", "api") and not (
                        out.requested_by.startswith("system:")
                    ):
                        await notify(
                            s,
                            user_id=out.requested_by,
                            kind="research_resumed",
                            account_id=out.account_id,
                            job_id=out.job_id,
                        )
                    await _audit(s, out.job_id, now)
                    continued += 1
                    if out.status == "queued":
                        start = out.job_id
            except Exception as error:  # noqa: BLE001 - one run never stops the pass
                _log.warning(
                    "runs.auto_resume_failed",
                    extra={
                        "tenant_id": str(tenant),
                        "run_id": str(run_id),
                        "error_class": type(error).__name__,
                    },
                )
                continue
            if start is not None:
                await jobs.run_or_enqueue(tenant, start)
    return {"continued": continued, "skipped": skipped}
