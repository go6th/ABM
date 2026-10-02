"""The pure row transition table and guard (section 5.2, proposal D-13). No I/O, no clock.

The table itself lives in ``config/tracker_v1.yaml`` (``transitions``); this module enforces it.
Order of checks, each raising a typed error and changing nothing:

1. the cell is ``no`` or absent: ``IllegalTransition``;
2. actor rules (T7): the finalizer may only sweep an unset row to Not done with a ``swept_*``
   reason; the judge and the gate may only take a downgrade cell; a stage must own the row and
   the verifier must be allowed on it, otherwise ``NotOwner``;
3. same status and same proof hash: ``Unchanged`` (no event, no bump);
4. proof is cumulative (T8): the new references must include the old ones;
5. the cell's evidence condition (new proof, a failed call, a successful call, results).

Downgrades keep the old proof, add a note and a reason code, and never produce Blocked or None.
"""

from __future__ import annotations

from dataclasses import dataclass

from account_intelligence.tracker.config import TrackerConfig, load_tracker_config
from account_intelligence.tracker.errors import IllegalTransition, NotOwner
from account_intelligence.tracker.model import SWEEP_REASONS, Actor, Proof, ReasonCode, Status

_DOWNGRADE = frozenset({"judge_or_gate_downgrade", "judge_or_gate_downgrade_only"})


@dataclass(frozen=True)
class TransitionResult:
    """The row after an accepted transition."""

    status: Status
    proof: Proof
    reason_code: ReasonCode | None
    unchanged: bool = False
    downgraded: bool = False


def transition_row(
    current_status: Status | None,
    current_proof: Proof | None,
    target_status: Status,
    new_proof: Proof,
    actor: Actor,
    is_owner: bool,
    has_failed_call: bool,
    has_successful_call: bool,
    has_results: bool,
    reason_code: ReasonCode | None = None,
    config: TrackerConfig | None = None,
) -> TransitionResult:
    """Validate one transition. ``is_owner`` means: for a stage, it owns the row; for the
    verifier, the row is one it may report. It is ignored for judge, gate and finalizer."""
    table = (config or load_tracker_config()).transitions
    source = current_status.value if current_status else "unset"
    allowed = table.get(source, {}).get(target_status.value, [])
    if not allowed or "no" in allowed:
        raise IllegalTransition(f"transition {source} -> {target_status.value} is not allowed")

    if actor is Actor.FINALIZER:
        if current_status is not None or target_status is not Status.NOT_DONE:
            raise NotOwner("the finalizer may only sweep an unset row to not_done")
        if reason_code not in SWEEP_REASONS or "finalizer" not in allowed:
            raise IllegalTransition("a sweep needs a swept_* reason code")
        return TransitionResult(target_status, new_proof, reason_code)

    if actor in (Actor.JUDGE, Actor.GATE):
        if not _DOWNGRADE.intersection(allowed):
            raise NotOwner(f"{actor.value} may only downgrade a row")
        if current_proof is None or reason_code is None:
            raise IllegalTransition("a downgrade needs an existing proof and a reason code")
        if target_status in (Status.NONE, Status.BLOCKED):  # pragma: no cover - table forbids
            raise IllegalTransition("a downgrade never produces none or blocked")
        note = f"downgraded {source}->{target_status.value} by {actor.value}: {reason_code.value}"
        return TransitionResult(
            target_status, current_proof.with_note(note), reason_code, downgraded=True
        )

    if not is_owner:
        raise NotOwner(f"{actor.value} does not own this row")

    if (
        current_status is not None
        and current_proof is not None
        and current_status == target_status
        and current_proof.proof_hash == new_proof.proof_hash
    ):
        return TransitionResult(current_status, current_proof, reason_code, unchanged=True)

    old_refs = set(current_proof.call_refs) if current_proof else set()
    new_refs = set(new_proof.call_refs)
    if not old_refs <= new_refs:
        raise IllegalTransition("proof is cumulative: the new proof dropped earlier calls")
    is_new_proof = bool(new_refs - old_refs)

    checks = {
        "stage": actor is Actor.STAGE,
        "verifier": actor is Actor.VERIFIER,
        "new_proof": is_new_proof,
        "extend_proof": is_new_proof,
        "unchanged_only": not is_new_proof,
        "new_proof_with_failed_call": is_new_proof and has_failed_call,
        "new_proof_successful_call": is_new_proof and has_successful_call,
        "new_proof_with_results": is_new_proof and has_results,
    }
    if not any(checks.get(condition, False) for condition in allowed):
        raise IllegalTransition(
            f"transition {source} -> {target_status.value} needs one of {sorted(allowed)}"
        )
    return TransitionResult(target_status, new_proof, reason_code)
