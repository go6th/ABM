"""``TrackerService``: the only write path for coverage rows.

Guarantees (each has a lying-stage test in ``tests/tracker/test_lying_stages.py``):

* T1: ``record_outcome`` takes call references, never a status; the status is derived by code.
* T2: every reference is resolved through the ledger for this tenant and run; anything else is
  ``ProofMismatch``.
* T7: a stage may write only the rows it owns (``owner_stage`` pinned at seeding); the verifier
  only its configured rows; judge and gate only through ``downgrade``; the finalizer only
  through ``sweep``.
* T8: the new proof must contain the old proof's references.
* T9: detail is internal only, control characters stripped, length capped, and refused if it
  names a broker, a credit figure or another account.
* Idempotent: the same evidence again returns ``unchanged`` and writes nothing.

All writes go through ``CoverageRepo`` on the caller's tenant transaction. Version conflicts are
re-read and re-evaluated at most three times (the one retry owner for that failure class).
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from account_intelligence.safety.client_output import scan_text
from account_intelligence.tracker.completion import CompletionVerdict, evaluate
from account_intelligence.tracker.config import (
    ChecklistConfig,
    TrackerConfig,
    load_checklist,
    load_tracker_config,
)
from account_intelligence.tracker.derive import derive_outcome
from account_intelligence.tracker.errors import (
    IllegalTransition,
    NotOwner,
    ProofMismatch,
    UnsafeDetail,
    VersionConflict,
)
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import (
    SWEEP_REASONS,
    Actor,
    CallRecord,
    Proof,
    ReasonCode,
    Status,
)
from account_intelligence.tracker.ports import CallLedgerPort, ChecklistSource, Clock, RunInputPort
from account_intelligence.tracker.render import render_client_view, render_operator_view
from account_intelligence.tracker.repo import CoverageRepo

MAX_CONFLICT_ATTEMPTS = 3


@dataclass(frozen=True)
class RecordResult:
    """What ``record_outcome`` or ``downgrade`` did."""

    row_key: str
    status: Status
    reason_code: ReasonCode | None
    unchanged: bool


class _ConfigChecklist:
    def get_checklist(self) -> ChecklistConfig:
        return load_checklist()


def sanitise_detail(
    detail: str | None, max_chars: int, forbidden_terms: Sequence[str]
) -> str | None:
    """T9: strip control characters, cap the length, refuse broker names, credit figures and
    other accounts' names. Raises ``UnsafeDetail``; never returns a partly scrubbed string."""
    if detail is None:
        return None
    cleaned = "".join(c for c in detail if unicodedata.category(c)[0] != "C" or c == " ")
    cleaned = " ".join(cleaned.split())[:max_chars]
    if scan_text(cleaned, forbidden_terms):
        raise UnsafeDetail("detail names a broker, a credit figure or another account")
    return cleaned or None


class TrackerService:
    """Seed, record, downgrade, sweep, verdict and view for one tenant transaction."""

    def __init__(
        self,
        repo: CoverageRepo,
        ledger: CallLedgerPort,
        run_input: RunInputPort,
        checklist: ChecklistSource | None = None,
        clock: Clock | None = None,
        *,
        owners: Mapping[str, str] | None = None,
        expected_calls: Mapping[str, int] | None = None,
        config: TrackerConfig | None = None,
    ):
        self.repo = repo
        self.ledger = ledger
        self.run_input = run_input
        self.checklist = checklist or _ConfigChecklist()
        self.clock = clock
        self.owners = dict(owners or {})
        self.expected_calls = dict(expected_calls or {})
        self.config = config or load_tracker_config()

    async def seed(self, run_id: UUID, tenant_id: UUID) -> bool:
        """Seed all rows unset and pin the checklist hash and policy. Re-seeding is a no-op."""
        cfg = self.checklist.get_checklist()
        return await self.repo.seed(
            run_id,
            tenant_id,
            cfg.rows,
            cfg.version,
            cfg.sha256,
            self.config.d_12_completion_policy,
            self.owners,
        )

    async def _resolve(
        self, tenant_id: UUID, run_id: UUID, call_refs: Sequence[str]
    ) -> list[CallRecord]:
        records = []
        for ref in dict.fromkeys(call_refs):
            try:
                record = await self.ledger.get_call_record(tenant_id, run_id, ref)
            except KeyError:
                raise ProofMismatch("a call reference is not a call of this run") from None
            if (record.tenant_id not in (None, str(tenant_id))) or (
                record.run_id not in (None, str(run_id))
            ):
                raise ProofMismatch("a call record belongs to another run or tenant")
            records.append(record)
        return records

    def _authorise(self, actor: Actor, stage: str | None, owner: str | None, row_key: str) -> None:
        if actor is Actor.STAGE:
            if stage is None or owner is None or stage != owner:
                raise NotOwner(f"stage {stage!r} does not own {row_key}")
        elif actor is Actor.VERIFIER:
            if row_key not in self.config.verifier_rows:
                raise NotOwner(f"the verifier may not report {row_key}")
        elif actor in (Actor.JUDGE, Actor.GATE):
            raise NotOwner(f"{actor.value} may only downgrade (use downgrade())")
        else:
            raise NotOwner("the finalizer may only sweep (use sweep())")

    async def record_outcome(
        self,
        tenant_id: UUID,
        run_id: UUID,
        row_key: str,
        call_refs: Sequence[str],
        actor: Actor,
        not_attempted_reason: ReasonCode | None = None,
        *,
        stage: str | None = None,
        expected_calls: int | None = None,
        cap_hit: bool = False,
        detail: str | None = None,
    ) -> RecordResult:
        """Derive and record a row's status from ledger evidence (T1, T2, T7, T8, T9)."""
        row_cfg = self.checklist.get_checklist().row(row_key)
        floor = self.expected_calls.get(row_key, 1)
        expected = max(floor, expected_calls or 1)
        for _ in range(MAX_CONFLICT_ATTEMPTS):
            stored = await self.repo.get_row(run_id, row_key)
            self._authorise(actor, stage, stored.owner_stage, row_key)
            if stored.state.swept:
                raise IllegalTransition(f"{row_key} was swept at a terminal state (T12)")
            records = await self._resolve(tenant_id, run_id, call_refs)
            scope = True
            if row_cfg is not None and row_cfg.lane:
                scope = await self.run_input.is_lane_in_scope(tenant_id, run_id, row_cfg.lane)
            outcome = derive_outcome(
                expected, records, cap_hit, scope, not_attempted_reason, self.config
            )
            proof = Proof.from_calls(records)
            result = transition_row(
                stored.state.status,
                stored.proof,
                outcome.status,
                proof,
                actor,
                True,
                any(not c.success for c in records),
                any(c.success for c in records),
                sum(c.result_count or 0 for c in records if c.success) > 0,
                outcome.reason,
                self.config,
            )
            if result.unchanged:
                return RecordResult(row_key, result.status, stored.state.reason_code, True)
            clean = sanitise_detail(
                detail,
                self.config.detail_max_chars,
                await self.run_input.other_account_names(tenant_id, run_id),
            )
            try:
                await self.repo.update_row(
                    run_id,
                    tenant_id,
                    row_key,
                    stored.version,
                    stored.state.status,
                    result.status,
                    result.proof,
                    result.reason_code,
                    actor,
                    stage=stage,
                    bump_attempt=True,
                    exhausted_at=self.config.attempts_per_row,
                    detail=clean,
                )
            except VersionConflict:
                continue
            return RecordResult(row_key, result.status, result.reason_code, False)
        raise VersionConflict(f"{row_key}: still conflicting after {MAX_CONFLICT_ATTEMPTS} reads")

    async def downgrade(
        self,
        tenant_id: UUID,
        run_id: UUID,
        row_key: str,
        target_status: Status,
        actor: Actor,
        reason_code: ReasonCode,
        *,
        stage: str | None = None,
    ) -> RecordResult:
        """Judge or gate downgrade (5.2, T14): keeps the old proof, adds a note and the reason."""
        if actor not in (Actor.JUDGE, Actor.GATE):
            raise NotOwner("only the judge and the gate may downgrade")
        for _ in range(MAX_CONFLICT_ATTEMPTS):
            stored = await self.repo.get_row(run_id, row_key)
            if stored.state.swept:
                raise IllegalTransition(f"{row_key} was swept at a terminal state (T12)")
            result = transition_row(
                stored.state.status,
                stored.proof,
                target_status,
                stored.proof or Proof(),
                actor,
                False,
                False,
                False,
                False,
                reason_code,
                self.config,
            )
            try:
                await self.repo.update_row(
                    run_id,
                    tenant_id,
                    row_key,
                    stored.version,
                    stored.state.status,
                    result.status,
                    result.proof,
                    result.reason_code,
                    actor,
                    stage=stage,
                    bump_attempt=False,
                    exhausted_at=self.config.attempts_per_row,
                )
            except VersionConflict:
                continue
            return RecordResult(row_key, result.status, result.reason_code, False)
        raise VersionConflict(f"{row_key}: still conflicting after {MAX_CONFLICT_ATTEMPTS} reads")

    async def downgrade_for_dropped_claims(
        self,
        tenant_id: UUID,
        run_id: UUID,
        row_key: str,
        surviving_call_refs: Sequence[str],
        actor: Actor,
    ) -> RecordResult | None:
        """T14: re-derive from the calls whose claims survived; downgrade when that is worse.
        A downgrade never produces None or Blocked: no surviving evidence means Not done."""
        stored = await self.repo.get_row(run_id, row_key)
        if stored.state.status not in (Status.DONE, Status.PARTIAL):
            return None
        records = await self._resolve(tenant_id, run_id, surviving_call_refs)
        derived = derive_outcome(
            self.expected_calls.get(row_key, 1),
            records,
            False,
            True,
            ReasonCode.CLAIMS_DROPPED,
            self.config,
        ).status
        target = derived if derived in (Status.DONE, Status.PARTIAL) else Status.NOT_DONE
        if target is stored.state.status or target is Status.DONE:
            return None
        return await self.downgrade(
            tenant_id, run_id, row_key, target, actor, ReasonCode.CLAIMS_DROPPED
        )

    async def sweep(self, run_id: UUID, tenant_id: UUID, reason: ReasonCode) -> list[str]:
        """T12: at a terminal state every unset row becomes Not done, swept, with ``reason``.
        Returns the swept keys. A row written concurrently is re-read, never overwritten."""
        if reason not in SWEEP_REASONS:
            raise IllegalTransition("a sweep needs a swept_* reason code")
        swept: list[str] = []
        for row in await self.repo.get_all_rows(run_id):
            if row.status is not None:
                continue
            for _ in range(MAX_CONFLICT_ATTEMPTS):
                stored = await self.repo.get_row(run_id, row.key)
                if stored.state.status is not None:
                    break
                result = transition_row(
                    None,
                    None,
                    Status.NOT_DONE,
                    Proof(),
                    Actor.FINALIZER,
                    False,
                    False,
                    False,
                    False,
                    reason,
                    self.config,
                )
                try:
                    await self.repo.update_row(
                        run_id,
                        tenant_id,
                        row.key,
                        stored.version,
                        None,
                        result.status,
                        result.proof,
                        reason,
                        Actor.FINALIZER,
                        stage=None,
                        bump_attempt=False,
                        exhausted_at=self.config.attempts_per_row,
                        swept=True,
                    )
                except VersionConflict:
                    continue
                swept.append(row.key)
                break
        return swept

    async def verdict(self, run_id: UUID) -> CompletionVerdict:
        """The completion verdict under the run's PINNED policy (never today's config)."""
        meta = await self.repo.get_meta(run_id)
        rows = await self.repo.get_all_rows(run_id)
        if meta is None:
            return evaluate(rows, {}, None)
        return evaluate(rows, meta["pinned_policy"], meta["seeded_row_count"])

    async def view(self, run_id: UUID, audience: str) -> dict[str, Any]:
        """``client`` (allow-listed, leak-scanned) or ``operator`` (admin role only)."""
        rows = await self.repo.get_all_rows(run_id)
        if audience == "client":
            return render_client_view(rows, await self.repo.get_proofs(run_id))
        if audience == "operator":
            return render_operator_view(rows, await self.repo.get_proofs(run_id))
        raise ValueError("audience must be client or operator")

    async def digest(self, run_id: UUID) -> str:
        """``tracker_digest`` for the checkpoint: hash of sorted (key, status, proof hash)."""
        rows = await self.repo.get_all_rows(run_id)
        body = "|".join(
            f"{r.key}:{r.status.value if r.status else '-'}:{r.proof_hash or '-'}"
            for r in sorted(rows, key=lambda r: r.key)
        )
        return hashlib.sha256(body.encode()).hexdigest()
