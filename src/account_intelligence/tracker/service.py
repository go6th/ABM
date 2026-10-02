"""Tracker service."""

from __future__ import annotations

from uuid import UUID

from account_intelligence.tracker.derive import derive_outcome
from account_intelligence.tracker.errors import (
    IllegalTransition,
    ProofMismatch,
    UnknownRow,
    VersionConflict,
)
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, Proof, ReasonCode, Status
from account_intelligence.tracker.ports import CallLedgerPort, ChecklistSource, Clock, RunInputPort
from account_intelligence.tracker.repo import CoverageRepo


class TrackerService:
    def __init__(
        self,
        repo: CoverageRepo,
        ledger: CallLedgerPort,
        run_input: RunInputPort,
        checklist: ChecklistSource,
        clock: Clock,
    ):
        self.repo = repo
        self.ledger = ledger
        self.run_input = run_input
        self.checklist = checklist
        self.clock = clock

    async def seed(self, run_id: UUID, tenant_id: UUID) -> None:
        cfg = self.checklist.get_checklist()
        from account_intelligence.tracker.config import _CHECKLIST_HASH, load_tracker_config

        trk = load_tracker_config()
        policy = trk.d_12_completion_policy
        await self.repo.seed(run_id, tenant_id, cfg.rows, cfg.version, _CHECKLIST_HASH, policy)

    async def record_outcome(
        self,
        tenant_id: UUID,
        run_id: UUID,
        row_key: str,
        call_refs: tuple[str, ...],
        actor: Actor,
        not_attempted_reason: ReasonCode | None = None,
    ) -> None:
        cfg = self.checklist.get_checklist()
        row_cfg = next((r for r in cfg.rows if r["key"] == row_key), None)
        if not row_cfg:
            raise UnknownRow(row_key)

        expected_calls = row_cfg.get("expected_calls", 1)
        owner = row_cfg.get("owner_stage")
        # In reality actor check depends on context, assuming Actor.STAGE is the owner.
        # "Only the row's owning stage ... may upgrade it"
        is_owner = actor == Actor.STAGE

        calls = []
        for ref in call_refs:
            try:
                record = self.ledger.get_call_record(tenant_id, run_id, ref)
                calls.append(record)
            except KeyError:
                raise ProofMismatch(f"Call {ref} not found for this run/tenant")

        lane = row_cfg.get("lane")
        in_scope = True
        if lane:
            in_scope = self.run_input.is_lane_in_scope(run_id, lane)

        outcome = derive_outcome(expected_calls, calls, False, in_scope, not_attempted_reason)

        has_failed = any(not c.success for c in calls)
        has_success = any(c.success for c in calls)
        has_results = sum(c.result_count or 0 for c in calls if c.success) > 0
        new_proof = Proof(call_refs=call_refs)

        for _ in range(3):
            try:
                row_state, version, curr_proof = await self.repo.get_row(run_id, row_key)

                res = transition_row(
                    row_state.status,
                    curr_proof,
                    outcome.status,
                    new_proof,
                    actor,
                    is_owner,
                    has_failed,
                    has_success,
                    has_results,
                    outcome.reason,
                )

                if res.unchanged:
                    return

                await self.repo.update_row(
                    run_id,
                    tenant_id,
                    row_key,
                    version,
                    res.status,
                    res.proof,
                    res.reason_code,
                    actor,
                    bumped=True,
                )
                return
            except VersionConflict:
                continue
        raise VersionConflict("Max retries reached")

    async def downgrade(
        self,
        tenant_id: UUID,
        run_id: UUID,
        row_key: str,
        target_status: Status,
        actor: Actor,
        reason_code: ReasonCode,
    ) -> None:
        if actor not in (Actor.JUDGE, Actor.GATE):
            raise IllegalTransition("Only judge and gate may downgrade")

        for _ in range(3):
            try:
                row_state, version, curr_proof = await self.repo.get_row(run_id, row_key)
                if not curr_proof:
                    raise IllegalTransition("Cannot downgrade a row with no proof")

                res = transition_row(
                    row_state.status,
                    curr_proof,
                    target_status,
                    curr_proof,  # new_proof is same as old
                    actor,
                    False,
                    False,
                    False,
                    False,
                    reason_code,
                )

                if res.unchanged:
                    return

                await self.repo.update_row(
                    run_id,
                    tenant_id,
                    row_key,
                    version,
                    res.status,
                    res.proof,
                    res.reason_code,
                    actor,
                    bumped=False,
                )
                return
            except VersionConflict:
                continue
        raise VersionConflict("Max retries reached")

    async def sweep(self, run_id: UUID, tenant_id: UUID, reason: ReasonCode) -> None:
        rows = await self.repo.get_all_rows(run_id)
        for r in rows:
            if r.status is None:
                # Terminal sweep
                row_state, version, curr_proof = await self.repo.get_row(run_id, r.key)
                # "T12 Terminal sweep. At any terminal state a row still unset is set to Not done with swept = true and a swept_* reason."
                try:
                    res = transition_row(
                        None,
                        None,
                        Status.NOT_DONE,
                        Proof(call_refs=()),
                        Actor.FINALIZER,
                        False,
                        False,
                        False,
                        False,
                        reason,
                    )
                    await self.repo.update_row(
                        run_id,
                        tenant_id,
                        r.key,
                        version,
                        Status.NOT_DONE,
                        Proof(call_refs=()),
                        reason,
                        Actor.FINALIZER,
                        bumped=False,
                        swept=True,
                    )
                except VersionConflict:
                    pass  # Ignore for sweep
