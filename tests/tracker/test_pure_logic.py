"""Tracker pure logic: transitions, derivation, completion, tally and rendering order."""

import pytest

from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.derive import Outcome, derive_outcome
from account_intelligence.tracker.errors import IllegalTransition, ProofIncomplete
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, CallRecord, Proof, ReasonCode, Status
from account_intelligence.tracker.tally import completeness, sort_for_rendering, tally


def test_machine_transition_table():
    # test some valid and invalid combinations
    proof = Proof(call_refs=("call1",))
    new_proof = Proof(call_refs=("call1", "call2"))

    # unset -> done by stage
    res = transition_row(None, None, Status.DONE, proof, Actor.STAGE, True, False, True, True)
    assert res.status == Status.DONE

    # unset -> blocked by stage
    res = transition_row(None, None, Status.BLOCKED, proof, Actor.STAGE, True, True, False, False)
    assert res.status == Status.BLOCKED

    # blocked -> not_done not allowed
    with pytest.raises(IllegalTransition):
        transition_row(
            Status.BLOCKED,
            proof,
            Status.NOT_DONE,
            new_proof,
            Actor.STAGE,
            True,
            False,
            False,
            False,
        )

    # done -> partial by judge downgrade
    res = transition_row(
        Status.DONE,
        proof,
        Status.PARTIAL,
        proof,
        Actor.JUDGE,
        False,
        False,
        False,
        False,
        reason_code=ReasonCode.CLAIMS_DROPPED,
    )
    assert res.status == Status.PARTIAL
    assert res.downgraded is True

    # done -> none by judge downgrade not allowed
    with pytest.raises(IllegalTransition):
        transition_row(
            Status.DONE, proof, Status.NONE, proof, Actor.JUDGE, False, False, False, False
        )


def test_derive_outcome():
    # 1. Scope out
    assert derive_outcome(1, [], False, False, ReasonCode.LANE_NOT_IN_SCOPE) == Outcome(
        Status.NONE, ReasonCode.OUT_OF_SCOPE
    )

    # 2. No calls
    assert derive_outcome(1, [], False, True, ReasonCode.DEADLINE) == Outcome(
        Status.NOT_DONE, ReasonCode.DEADLINE
    )
    with pytest.raises(ProofIncomplete):
        derive_outcome(1, [], False, True, None)

    # 3. Blocked
    c1 = CallRecord(call_ref="1", tool_category="t1", success=False, error_kind="rate_limit")
    assert derive_outcome(1, [c1], False, True, None) == Outcome(
        Status.BLOCKED, ReasonCode.SOURCE_FAILED
    )  # Fallback because enum matching might be loose in simple test

    # 4. None
    c2 = CallRecord(call_ref="2", tool_category="t1", success=True, result_count=0)
    assert derive_outcome(1, [c2], False, True, None) == Outcome(Status.NONE)

    # 5. Done
    c3 = CallRecord(call_ref="3", tool_category="t1", success=True, result_count=1)
    assert derive_outcome(1, [c3], False, True, None) == Outcome(Status.DONE)

    # 5. Partial
    assert derive_outcome(2, [c3], False, True, None) == Outcome(
        Status.PARTIAL, ReasonCode.FEWER_THAN_EXPECTED
    )

    # Cap hit
    assert derive_outcome(1, [c3], True, True, None) == Outcome(
        Status.PARTIAL, ReasonCode.CAP_REACHED
    )


def test_evaluate():
    rows = [
        RowState("P1.01", "high", Status.DONE, None, "hash", False),
        RowState("P2.15", "medium", Status.PARTIAL, None, "hash", False),
    ]
    policy = {"high_rows_allowed_for_completed": ["done", "none"]}
    verdict = evaluate(rows, policy)
    assert verdict.can_complete is True
    assert verdict.level == "complete_with_gaps"  # Medium is partial

    # Add a high not_done
    rows.append(RowState("P1.02", "high", Status.NOT_DONE, ReasonCode.DEADLINE, "hash", False))
    verdict2 = evaluate(rows, policy)
    assert verdict2.can_complete is False
    assert len(verdict2.blockers) == 1
    assert verdict2.blockers[0]["key"] == "P1.02"


def test_tally():
    rows = [
        RowState("prior_customer_status", "high", Status.DONE, None, "hash", False),
        RowState("basic_fit_check", "high", Status.NONE, None, "hash", False),
        RowState("company_news", "high", Status.PARTIAL, None, "hash", False),
        RowState("events_sponsored", "optional", Status.NOT_DONE, None, "hash", False),
        RowState("podcasts", "medium", Status.BLOCKED, None, "hash", False),
    ]
    t = tally(rows)
    assert t["status_counts"]["done"] == 1
    assert t["status_counts"]["none"] == 1
    assert t["status_counts"]["partial"] == 1
    assert t["status_counts"]["not_done"] == 1
    assert t["status_counts"]["blocked"] == 1
    assert t["high_base"] == 19
    assert t["medium_base"] == 7
    assert t["optional_base"] == 7


def test_completeness():
    rows = [
        RowState("prior_customer_status", "high", Status.DONE, None, "hash", False),
        RowState("basic_fit_check", "high", Status.NONE, None, "hash", False),
        RowState("company_news", "high", Status.PARTIAL, None, "hash", False),
    ]
    ratio, base = completeness(rows)
    assert base == 3
    assert ratio == (2 / 3)


def test_sort_for_rendering():
    rows = [
        RowState("events_sponsored", "optional", Status.NOT_DONE, None, "hash", False),
        RowState("basic_fit_check", "high", Status.NONE, None, "hash", False),
        RowState("company_news", "high", Status.PARTIAL, None, "hash", False),
    ]
    sorted_rows = sort_for_rendering(rows)
    # open work first -> company_news (partial, high), events_sponsored (not_done, optional)
    # then basic_fit_check (none, high)
    assert sorted_rows[0].key == "company_news"
    assert sorted_rows[1].key == "events_sponsored"
    assert sorted_rows[2].key == "basic_fit_check"
