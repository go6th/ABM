"""``TrackerPort`` on ``TrackerService`` (statuses are derived there from ledger evidence).

* ``report`` records a row as its owning workflow stage (``spec.coverage_owners``) with the
  manifest's call references as proof, or with a configured not-attempted reason; the agent
  layer's reasons map onto the tracker's ``ReasonCode`` (consent, spend, source failures).
* ``mark_partial`` is the tracker's downgrade (actor ``gate``: the approval gate) with
  ``pull_not_approved``; a Blocked row keeps its status.
* ``InheritingCallLedger`` resolves a proof reference in this run, then in the run's declared
  parent pull run (a Phase 2 run's rows are proven by the pull it was built on, G16). The parent's
  real ledger record is returned for this run; a reference in neither run is refused.

GUARANTEE(test_full_abm_run_grades_through_the_engine_and_tracks_every_row)
GUARANTEE(test_inherited_proof_comes_only_from_the_declared_parent)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.repo import TxnFactory
from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
from account_intelligence.tracker.model import Actor, CallRecord, ReasonCode, Status
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.service import TrackerService
from agents.ports import RowOutcome, RunContext
from agents.production.grading import parent_run

REASONS: dict[str, ReasonCode] = {
    "consent_required": ReasonCode.CONSENT_REQUIRED,
    "spend_not_approved": ReasonCode.NO_CAP,
    "no_cap": ReasonCode.NO_CAP,
    # the run used the credits confirmed for it (credit cap per pull)
    "cap_reached": ReasonCode.BUDGET_CAP,
    "budget_cap": ReasonCode.BUDGET_CAP,
    "credits_exhausted": ReasonCode.CREDITS_EXHAUSTED,
    "not_supported_yet": ReasonCode.NOT_AVAILABLE_YET,
    "needs_profile_urls": ReasonCode.DEPENDENCY_FAILED,
    "agent_budget_exceeded": ReasonCode.TOOL_CALL_CAP,
    "pull_not_approved": ReasonCode.PULL_NOT_APPROVED,
    # outside a research job's scope (a jobs-only or people-only refresh)
    "not_planned": ReasonCode.NOT_PLANNED,
    # internal history without internal access (the skill skips it; never web-searched)
    "no_internal_access": ReasonCode.NO_INTERNAL_ACCESS,
    # the people step's findings cannot be saved yet, so its pulls are not made (OI-ABM-4)
    "people_not_recorded": ReasonCode.NOT_AVAILABLE_YET,
}


def reason_for(code: str | None) -> ReasonCode | None:
    """The tracker reason of an agent-layer reason (any other failure: ``source_failed``)."""
    if code is None:
        return None
    return REASONS.get(code, ReasonCode.SOURCE_FAILED)


@dataclass
class InheritingCallLedger:
    """``CallLedgerPort``: this run's ledger, then its declared parent's (read only)."""

    inner: LedgerCallAdapter
    parent: UUID | None

    async def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        """The record of a call of this run, or of the declared parent pull run."""
        try:
            return await self.inner.get_call_record(tenant_id, run_id, call_ref)
        except KeyError:
            if self.parent is None:
                raise
        record = await self.inner.get_call_record(tenant_id, self.parent, call_ref)
        return record.model_copy(update={"run_id": str(run_id)})


def _outcome(status: Status | None, reason: ReasonCode | None, calls: int) -> RowOutcome:
    return RowOutcome(
        status.value if status is not None else None,
        reason.value if reason is not None else None,
        calls,
    )


@dataclass(frozen=True)
class TrackerAdapter:
    """See the module docstring."""

    txn: TxnFactory
    owners: Mapping[str, str]

    async def _service(self, s: AsyncSession, ctx: RunContext) -> TrackerService:
        ledger = LedgerCallAdapter(s, load_grading_config().category_by_operation)
        parent = await parent_run(self.txn, ctx.tenant_id, ctx.run_id)
        return TrackerService(
            CoverageRepo(s),
            InheritingCallLedger(ledger, parent),
            RunInputAdapter(s),
            owners=self.owners,
        )

    async def report(
        self, ctx: RunContext, row_key: str, call_refs: Sequence[str], reason: str | None
    ) -> RowOutcome:
        """Record the row's evidence (or its not-attempted reason) as its owning stage. A row is
        reported again by a follow-up pull: its proof is cumulative, so the calls it already
        recorded are kept and the new ones added."""
        async with self.txn(ctx.tenant_id) as s:
            service = await self._service(s, ctx)
            stored = await service.repo.get_row(ctx.run_id, row_key)
            prior = stored.proof.call_refs if stored.proof is not None else ()
            refs = tuple(dict.fromkeys((*prior, *call_refs)))
            result = await service.record_outcome(
                ctx.tenant_id,
                ctx.run_id,
                row_key,
                refs,
                Actor.STAGE,
                None if refs else reason_for(reason or "source_failed"),
                stage=self.owners.get(row_key),
            )
        return _outcome(result.status, result.reason_code, len(set(refs)))

    async def rows(self, ctx: RunContext) -> Mapping[str, RowOutcome]:
        """Every row of the run."""
        async with self.txn(ctx.tenant_id) as s:
            rows = await CoverageRepo(s).get_all_rows(ctx.run_id)
        return {r.key: _outcome(r.status, r.reason_code, r.proof_calls) for r in rows}

    async def mark_partial(self, ctx: RunContext, row_key: str, reason: str) -> RowOutcome:
        """Downgrade to Partial with ``pull_not_approved`` (a Blocked row stays Blocked)."""
        async with self.txn(ctx.tenant_id) as s:
            rows = {r.key: r for r in await CoverageRepo(s).get_all_rows(ctx.run_id)}
            row = rows.get(row_key)
            # the tracker downgrades only a Done row (or a Partial one is kept): a row that is
            # Not done or None already says what happened, with its reason, and a follow-up pull
            # that was not approved cannot make it "less" than that. Refusing here stopped the
            # whole run on 2026-10-24 (NotOwner: gate may only downgrade a row).
            if row is None or row.status in (
                None,
                Status.BLOCKED,
                Status.PARTIAL,
                Status.NOT_DONE,
                Status.NONE,
            ):
                return _outcome(
                    row.status if row else None,
                    row.reason_code if row else None,
                    row.proof_calls if row else 0,
                )
            service = await self._service(s, ctx)
            result = await service.downgrade(
                ctx.tenant_id,
                ctx.run_id,
                row_key,
                Status.PARTIAL,
                Actor.GATE,
                reason_for(reason) or ReasonCode.PULL_NOT_APPROVED,
            )
        return _outcome(result.status, result.reason_code, row.proof_calls)
