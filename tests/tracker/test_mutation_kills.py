"""Assertions added after the mutation check (section 9): each test pins behaviour a surviving
mutant changed without any test noticing (reasons, result fields, verdict contents, boundaries)."""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest

from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.derive import Outcome, blocked_reason, derive_outcome
from account_intelligence.tracker.errors import IllegalTransition, NotOwner
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, CallRecord, Proof, ReasonCode, Status

D, P, N, B, X = Status.DONE, Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED, Status.NONE


def _ok(i: int, results: int | None) -> CallRecord:
    return CallRecord(
        call_ref=f"ok{i}", tool_category="web_search", success=True, result_count=results
    )


def _fail(i: int, kind: str | None) -> CallRecord:
    return CallRecord(call_ref=f"f{i}", tool_category="web_search", success=False, error_kind=kind)


@pytest.mark.parametrize(
    ("expected", "ok", "failed", "results", "cap", "unknown"),
    list(itertools.product((1, 2), (1, 2), (0, 1), (0, 2), (False, True), (False, True))),
)
def test_derived_reason_matches_the_oracle(
    expected: int, ok: int, failed: int, results: int, cap: bool, unknown: bool
) -> None:
    calls = [_ok(i, None if (unknown and i == 0) else results) for i in range(ok)]
    calls += [_fail(i, "RateLimited") for i in range(failed)]
    got = derive_outcome(expected, calls, cap, True, None)
    counted = ok - (1 if unknown else 0)
    total = counted * results
    if total == 0:
        if not failed and not unknown and counted >= expected:
            want = Outcome(X)
        elif failed:
            want = Outcome(P, ReasonCode.SOURCE_FAILED)
        elif unknown:
            want = Outcome(P, ReasonCode.RESULT_COUNT_UNKNOWN)
        else:
            want = Outcome(P, ReasonCode.FEWER_THAN_EXPECTED)
    elif not failed and counted >= expected and not cap:
        want = Outcome(D)
    elif failed:
        want = Outcome(P, ReasonCode.SOURCE_FAILED)
    elif cap:
        want = Outcome(P, ReasonCode.CAP_REACHED)
    elif unknown:
        want = Outcome(P, ReasonCode.RESULT_COUNT_UNKNOWN)
    else:
        want = Outcome(P, ReasonCode.FEWER_THAN_EXPECTED)
    assert got == want


def test_scope_out_reason_and_blocked_reason_by_severity() -> None:
    assert derive_outcome(1, [], False, False, ReasonCode.LANE_NOT_IN_SCOPE).reason is (
        ReasonCode.OUT_OF_SCOPE
    )
    cfg = load_tracker_config()
    # the most severe class wins whatever the order: AuthError beats RateLimited
    for failed in (
        [_fail(1, "RateLimited"), _fail(2, "AuthError")],
        [_fail(2, "AuthError"), _fail(1, "RateLimited")],
    ):
        assert blocked_reason(failed, cfg) is ReasonCode.SOURCE_UNAVAILABLE
    assert blocked_reason([_fail(1, "RateLimited")], cfg) is ReasonCode.SOURCE_RATE_LIMITED
    assert (
        blocked_reason([_fail(1, "BudgetExceeded"), _fail(2, "RateLimited")], cfg)
        is ReasonCode.BUDGET_CAP
    )
    assert blocked_reason([_fail(1, None)], cfg) is ReasonCode.SOURCE_FAILED  # no class
    assert blocked_reason([_fail(1, "SomethingNew")], cfg) is ReasonCode.SOURCE_FAILED
    # an unknown class never outranks a known one
    assert (
        blocked_reason([_fail(1, "SomethingNew"), _fail(2, "RateLimited")], cfg)
        is ReasonCode.SOURCE_RATE_LIMITED
    )
    assert len({Outcome(D), Outcome(D), Outcome(P, ReasonCode.CAP_REACHED)}) == 2
    assert hash(Outcome(D)) != hash(Outcome(P, ReasonCode.CAP_REACHED))
    assert repr(Outcome(D)) == "Outcome(done, None)"


OLD = Proof(call_refs=("a",))
MORE = Proof(call_refs=("a", "b"))


def test_transition_results_carry_the_right_fields_and_messages() -> None:
    r = transition_row(
        None, None, D, MORE, Actor.STAGE, True, False, True, True, ReasonCode.CAP_REACHED
    )
    assert (r.status, r.proof, r.reason_code, r.unchanged, r.downgraded) == (
        D,
        MORE,
        ReasonCode.CAP_REACHED,
        False,
        False,
    )
    r = transition_row(
        N, OLD, D, MORE, Actor.STAGE, True, False, True, True, ReasonCode.SOURCE_FAILED
    )
    assert (r.status, r.proof, r.reason_code) == (D, MORE, ReasonCode.SOURCE_FAILED)
    same = transition_row(D, OLD, D, OLD, Actor.STAGE, True, False, True, True, ReasonCode.DEADLINE)
    assert (same.status, same.proof, same.reason_code, same.unchanged) == (
        D,
        OLD,
        ReasonCode.DEADLINE,
        True,
    )
    down = transition_row(
        D, OLD, P, OLD, Actor.JUDGE, False, False, False, False, ReasonCode.CLAIMS_DROPPED
    )
    assert (down.status, down.reason_code, down.downgraded) == (P, ReasonCode.CLAIMS_DROPPED, True)
    assert down.proof.call_refs == OLD.call_refs
    assert down.proof.notes == ("downgraded done->partial by judge: claims_dropped",)
    sweep = transition_row(
        None, None, N, Proof(), Actor.FINALIZER, False, False, False, False, ReasonCode.SWEPT_FAILED
    )
    assert (sweep.status, sweep.reason_code) == (N, ReasonCode.SWEPT_FAILED)
    with pytest.raises(IllegalTransition, match="not allowed"):
        transition_row(D, OLD, B, MORE, Actor.STAGE, True, True, False, False)
    with pytest.raises(NotOwner, match="finalizer may only sweep"):
        transition_row(None, None, D, MORE, Actor.FINALIZER, False, False, False, False)
    with pytest.raises(IllegalTransition, match="swept_"):
        transition_row(
            None, None, N, Proof(), Actor.FINALIZER, False, False, False, False, ReasonCode.DEADLINE
        )
    with pytest.raises(NotOwner, match="may only downgrade"):
        transition_row(None, None, D, MORE, Actor.GATE, False, False, True, True)
    with pytest.raises(IllegalTransition, match="existing proof and a reason"):
        transition_row(D, OLD, P, OLD, Actor.JUDGE, False, False, False, False, None)
    with pytest.raises(NotOwner, match="does not own"):
        transition_row(None, None, D, MORE, Actor.STAGE, False, False, True, True)
    with pytest.raises(IllegalTransition, match="cumulative"):
        transition_row(D, MORE, D, Proof(call_refs=("c",)), Actor.STAGE, True, False, True, True)
    with pytest.raises(IllegalTransition, match="needs one of"):
        transition_row(X, OLD, D, MORE, Actor.STAGE, True, False, True, False)


def _row(
    key: str,
    prio: str,
    status: Status | None,
    reason: ReasonCode | None = None,
    proof: str | None = "h",
    swept: bool = False,
) -> RowState:
    return RowState(key, prio, status, reason, proof if status else None, swept)


def test_verdict_contents_are_exact() -> None:
    rows = [
        _row("a", "high", D),
        _row("b", "high", None),
        _row("c", "high", N, ReasonCode.SWEPT_FAILED, swept=True),
        _row("d", "medium", P, ReasonCode.CAP_REACHED, proof=None),
        _row("e", "high", B, ReasonCode.SOURCE_UNAVAILABLE),
        _row("f", "optional", X),
    ]
    v = evaluate(rows, {"high_rows_allowed_for_completed": ["done", "none"]}, 7)
    assert v.blockers == [
        {"key": None, "status": "short", "reason": "row_count_mismatch"},
        {"key": "b", "status": "unset", "reason": None},
        {"key": "c", "status": "not_done", "reason": "swept_failed"},
        {"key": "d", "status": "partial", "reason": "missing_proof"},
        {"key": "e", "status": "blocked", "reason": "source_unavailable"},
    ]
    assert v.tally == {"done": 1, "partial": 1, "not_done": 1, "blocked": 1, "none": 1, "unset": 1}
    assert (v.completeness_ratio, v.completeness_base) == (0.25, 4)
    assert evaluate([], {}, None).blockers == [
        {"key": None, "status": "unseeded", "reason": "run_not_seeded"}
    ]
    assert evaluate([_row("m", "medium", D)], {}, 1).completeness_ratio == 1.0  # no High rows
    assert evaluate([], {}, 0).can_complete  # zero seeded rows and zero rows agree
    assert not evaluate([_row("m", "medium", D)], {}, 0).can_complete


def test_window_includes_the_as_of_day_and_counts_skip_every_unsearched_platform() -> None:
    from jobs.counts import compute_counts
    from jobs.models import NotSearched
    from jobs.window import classify_window

    as_of = date(2026, 10, 1)
    assert classify_window(as_of, as_of) == "in_window"
    assert classify_window(as_of + timedelta(days=1), as_of) == "out_of_window"
    counts = compute_counts(
        [], [], ["A", "B", "C"], {"A": "lane_not_in_scope", "B": "lane_not_in_scope"}
    )
    assert isinstance(counts.per_platform["A"], NotSearched)
    assert isinstance(counts.per_platform["B"], NotSearched)
    assert not isinstance(counts.per_platform["C"], NotSearched)


def test_not_attempted_and_blocked_carry_their_reasons() -> None:
    from account_intelligence.tracker.errors import ProofIncomplete

    got = derive_outcome(1, [], False, True, ReasonCode.NOT_PLANNED)
    assert got == Outcome(N, ReasonCode.NOT_PLANNED)
    with pytest.raises(ProofIncomplete, match="no not-attempted reason"):
        derive_outcome(1, [], False, True, None)
    assert derive_outcome(1, [_fail(1, "RateLimited")], False, True, None) == Outcome(
        B, ReasonCode.SOURCE_RATE_LIMITED
    )
    cfg = load_tracker_config()
    # severity, not the alphabet, decides: the more severe class sorts later by name here
    assert blocked_reason([_fail(1, "RateLimited"), _fail(2, "SecurityError")], cfg) is (
        ReasonCode.SOURCE_FAILED
    )
    # CreditsExhausted outranks BudgetExceeded; its reason is credits_exhausted (D-31)
    assert blocked_reason([_fail(1, "BudgetExceeded"), _fail(2, "CreditsExhausted")], cfg) is (
        ReasonCode.CREDITS_EXHAUSTED
    )


def test_sweep_keeps_its_proof_and_not_done_stays_not_done_without_new_proof() -> None:
    swept = Proof(notes=("swept",))
    r = transition_row(
        None, None, N, swept, Actor.FINALIZER, False, False, False, False, ReasonCode.SWEPT_DEADLINE
    )
    assert r.proof == swept
    stay = transition_row(
        N, OLD, N, OLD, Actor.STAGE, True, False, False, False, ReasonCode.NOT_PLANNED
    )
    assert stay.status is N and stay.unchanged
    with pytest.raises(IllegalTransition):
        transition_row(N, OLD, N, MORE, Actor.STAGE, True, False, True, False)


def test_tally_counts_every_row() -> None:
    rows = [_row("a", "medium", D), _row("b", "medium", D), _row("c", "medium", None)]
    assert evaluate(rows, {}, 3).tally["done"] == 2
