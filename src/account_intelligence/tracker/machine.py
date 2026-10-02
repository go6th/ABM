"""Tracker state machine (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.errors import IllegalTransition, NotOwner
from account_intelligence.tracker.model import Actor, Proof, ReasonCode, Status


@dataclass(frozen=True)
class TransitionResult:
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
) -> TransitionResult:
    config = load_tracker_config()
    table = config.transitions

    if current_status and current_proof:
        if current_status == target_status and current_proof.proof_hash == new_proof.proof_hash:
            return TransitionResult(current_status, current_proof, reason_code, unchanged=True)

        old_refs = set(current_proof.call_refs)
        new_refs = set(new_proof.call_refs)
        if not old_refs.issubset(new_refs):
            # new proof must be a superset
            # this check should probably be handled by caller or here. T8: Proof is cumulative.
            raise IllegalTransition("Proof is not cumulative. Call refs missing.")

        is_new_proof = len(new_refs - old_refs) > 0
    else:
        is_new_proof = len(new_proof.call_refs) > 0

    c_stat = current_status.value if current_status else "unset"
    t_stat = target_status.value

    allowed = table.get(c_stat, {}).get(t_stat, [])

    if not allowed or "no" in allowed:
        raise IllegalTransition(f"Transition {c_stat} -> {t_stat} not allowed.")

    if not is_owner and actor not in (Actor.VERIFIER, Actor.JUDGE, Actor.GATE, Actor.FINALIZER):
        raise NotOwner("Actor is not the owner or an authorized role.")

    # Evaluate allowed conditions
    valid = False
    is_downgrade = False

    for cond in allowed:
        if cond in ("stage", "verifier", "finalizer"):
            # Actor specific rules from table
            if (
                cond == "stage"
                and (actor == Actor.STAGE and is_owner)
                or cond == "verifier"
                and actor == Actor.VERIFIER
                or cond == "finalizer"
                and actor == Actor.FINALIZER
            ):
                valid = True
        elif cond == "new_proof":
            if is_new_proof:
                valid = True
        elif cond == "unchanged_only":
            if not is_new_proof:
                valid = True
        elif cond == "new_proof_with_failed_call":
            if is_new_proof and has_failed_call:
                valid = True
        elif cond == "new_proof_successful_call":
            if is_new_proof and has_successful_call:
                valid = True
        elif cond == "extend_proof":
            if is_new_proof:
                valid = True
        elif cond == "new_proof_with_results":
            if is_new_proof and has_results:
                valid = True
        elif cond in ("judge_or_gate_downgrade", "judge_or_gate_downgrade_only"):
            if actor in (Actor.JUDGE, Actor.GATE):
                valid = True
                is_downgrade = True

    if not valid:
        raise IllegalTransition(f"Transition {c_stat} -> {t_stat} failed conditions {allowed}.")

    # "Downgrades keep the old proof and add a downgrade note and a reason code"
    if is_downgrade:
        # T14: downgrade never produces None or Blocked
        if target_status in (Status.NONE, Status.BLOCKED):
            raise IllegalTransition("Downgrade cannot produce None or Blocked")
        # Keep old proof
        return TransitionResult(
            target_status, current_proof or new_proof, reason_code, downgraded=True
        )

    return TransitionResult(target_status, new_proof, reason_code)
