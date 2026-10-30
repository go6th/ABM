"""Kill tests for the second mutation run (2026-10-30) of the tracker's pure modules: the table
lookup of a transition, the sweep and downgrade refusals (exact text), the conditions a table row
may name, the reasons a High row may complete with, and the class an unclassified failure has.
Hermetic: the configuration is the repository's, changed through ``model_copy`` where a case the
production table never reaches must be reached."""

from __future__ import annotations

import pytest

from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.derive import blocked_reason, derive_outcome
from account_intelligence.tracker.errors import IllegalTransition, NotOwner, ProofIncomplete
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, CallRecord, Proof, ReasonCode, Status

D, P, N, B, X = Status.DONE, Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED, Status.NONE
OLD = Proof(call_refs=("a",))
MORE = Proof(call_refs=("a", "b"))
CFG = load_tracker_config()


def _fail(kind: str | None, ref: str = "f") -> CallRecord:
    return CallRecord(call_ref=ref, tool_category="web_search", success=False, error_kind=kind)


def _with_rules(**rules: object):  # type: ignore[no-untyped-def]
    return CFG.model_copy(update={"d_13_tracker_rules": {**CFG.d_13_tracker_rules, **rules}})


def _raises(exc: type[Exception], call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(exc) as caught:
        call()
    return str(caught.value)


# ---- derive -----------------------------------------------------------------------------------


def test_a_failure_without_a_class_maps_through_the_empty_class() -> None:
    cfg = _with_rules(error_reason_map={"": "source_unavailable"})
    assert blocked_reason([_fail(None)], cfg) is ReasonCode.SOURCE_UNAVAILABLE
    # with no entry for the empty class the failure is a plain source failure
    assert blocked_reason([_fail(None)], CFG) is ReasonCode.SOURCE_FAILED


def test_among_unknown_classes_the_empty_one_sorts_first() -> None:
    cfg = _with_rules(
        error_severity_order=["AuthError"],
        error_reason_map={"Abc": "tool_call_cap", "": "source_unavailable"},
    )
    assert (
        blocked_reason([_fail("Abc", "f1"), _fail(None, "f2")], cfg)
        is ReasonCode.SOURCE_UNAVAILABLE
    )
    assert (
        blocked_reason([_fail(None, "f2"), _fail("Abc", "f1")], cfg)
        is ReasonCode.SOURCE_UNAVAILABLE
    )


def test_no_calls_and_no_reason_is_refused_with_this_message() -> None:
    assert _raises(ProofIncomplete, lambda: derive_outcome(1, [], False, True, None)) == (
        "no calls and no not-attempted reason"
    )


# ---- machine ----------------------------------------------------------------------------------


def test_a_status_the_table_has_no_row_for_is_an_illegal_transition() -> None:
    cfg = CFG.model_copy(update={"transitions": {"unset": CFG.transitions["unset"]}})
    message = _raises(
        IllegalTransition,
        lambda: transition_row(D, OLD, D, MORE, Actor.STAGE, True, False, True, True, config=cfg),
    )
    assert message == "transition done -> done is not allowed"
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(
                None,
                None,
                B,
                MORE,
                Actor.STAGE,
                True,
                False,
                True,
                True,
                config=cfg.model_copy(update={"transitions": {"unset": {}}}),
            ),
        )
        == "transition unset -> blocked is not allowed"
    )


def test_the_sweep_and_downgrade_refusals_are_these_sentences() -> None:
    assert (
        _raises(
            NotOwner,
            lambda: transition_row(
                None, None, D, MORE, Actor.FINALIZER, False, False, False, False
            ),
        )
        == "the finalizer may only sweep an unset row to not_done"
    )
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(
                None,
                None,
                N,
                Proof(),
                Actor.FINALIZER,
                False,
                False,
                False,
                False,
                ReasonCode.DEADLINE,
            ),
        )
        == "a sweep needs a swept_* reason code"
    )
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(D, OLD, P, OLD, Actor.JUDGE, False, False, False, False, None),
        )
        == "a downgrade needs an existing proof and a reason code"
    )
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(
                D, None, P, OLD, Actor.JUDGE, False, False, False, False, ReasonCode.CLAIMS_DROPPED
            ),
        )
        == "a downgrade needs an existing proof and a reason code"
    )


def test_a_downgrade_never_produces_none_or_blocked_even_when_a_table_allowed_it() -> None:
    table = {
        **CFG.transitions,
        "done": {
            **CFG.transitions["done"],
            "none": ["judge_or_gate_downgrade"],
            "blocked": ["judge_or_gate_downgrade"],
        },
    }
    cfg = CFG.model_copy(update={"transitions": table})
    for target in (X, B):
        message = _raises(
            IllegalTransition,
            lambda target=target: transition_row(  # type: ignore[misc]
                D,
                OLD,
                target,
                OLD,
                Actor.GATE,
                False,
                False,
                False,
                False,
                ReasonCode.CLAIMS_DROPPED,
                config=cfg,
            ),
        )
        assert message == "a downgrade never produces none or blocked"


def test_other_refusals_are_these_sentences() -> None:
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(
                D, MORE, D, Proof(call_refs=("c",)), Actor.STAGE, True, False, True, True
            ),
        )
        == "proof is cumulative: the new proof dropped earlier calls"
    )
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(X, OLD, D, MORE, Actor.STAGE, True, False, True, False),
        )
        == "transition none -> done needs one of ['new_proof_with_results']"
    )
    assert (
        _raises(
            NotOwner,
            lambda: transition_row(None, None, D, MORE, Actor.STAGE, False, False, True, True),
        )
        == "stage does not own this row"
    )
    assert (
        _raises(
            NotOwner,
            lambda: transition_row(None, None, D, MORE, Actor.GATE, False, False, True, True),
        )
        == "gate may only downgrade a row"
    )


def test_a_row_may_be_restated_with_the_same_calls_and_another_note() -> None:
    first = Proof.from_calls(
        [CallRecord(call_ref="a", tool_category="web_search", success=True, result_count=1)]
    )
    noted = Proof.from_calls(
        [CallRecord(call_ref="a", tool_category="web_search", success=True, result_count=2)]
    )
    assert noted.proof_hash != first.proof_hash and noted.call_refs == first.call_refs
    # not_done -> not_done is allowed only with no new call (the table's ``unchanged_only``)
    kept = transition_row(N, first, N, noted, Actor.STAGE, True, False, False, False)
    assert (kept.status, kept.proof, kept.unchanged) == (N, noted, False)
    # an equal call set is still cumulative: partial -> done with the same calls needs a new one
    assert (
        _raises(
            IllegalTransition,
            lambda: transition_row(P, first, D, noted, Actor.STAGE, True, False, True, True),
        )
        == "transition partial -> done needs one of ['new_proof']"
    )


def test_extending_a_proof_is_allowed_where_the_table_says_so() -> None:
    more = transition_row(P, OLD, P, MORE, Actor.STAGE, True, False, True, True)
    assert (more.status, more.proof) == (P, MORE)


# ---- completion -------------------------------------------------------------------------------


def _row(key: str, status: Status, reason: ReasonCode | None = None) -> RowState:
    return RowState(key, "high", status, reason, "h", False)


def test_a_high_row_may_complete_with_a_listed_reason_only() -> None:
    policy = {
        "high_rows_allowed_for_completed": ["done", "none"],
        "high_reasons_allowed_for_completed": ["cap_reached"],
    }
    listed = evaluate([_row("a", P, ReasonCode.CAP_REACHED)], policy, 1)
    assert listed.can_complete and listed.blockers == []
    other = evaluate([_row("a", P, ReasonCode.SOURCE_FAILED)], policy, 1)
    assert [b["key"] for b in other.blockers] == ["a"]
    none = evaluate([_row("a", P, None)], policy, 1)
    assert none.blockers == [{"key": "a", "status": "partial", "reason": None}]
    unlisted = evaluate(
        [_row("a", P, ReasonCode.CAP_REACHED)], {"high_rows_allowed_for_completed": ["done"]}, 1
    )
    assert not unlisted.can_complete
    assert evaluate([_row("a", D)], policy, 1).can_complete
