"""Exhaustive pure-logic tests (spec 9.2): the 30-cell table against a hand-written oracle, the
``derive_outcome`` truth table and properties, and every ``evaluate`` combination for both
policies. The oracle below is typed from the spec's table in section 5.2, not computed from
``config/tracker_v1.yaml``."""

from __future__ import annotations

import itertools
from collections import Counter

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.config import CONFIG_DIR
from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.derive import derive_outcome
from account_intelligence.tracker.errors import ProofIncomplete, TrackerError
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, CallRecord, Proof, ReasonCode, Status

U = None
D, P, N, B, X = Status.DONE, Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED, Status.NONE
SOURCES = (U, N, B, P, X, D)
TARGETS = (D, P, N, B, X)

# Spec 5.2, typed by hand. Keys: (from, to). Values: the rule text from the spec.
ORACLE = {
    (U, D): "stage,verifier",
    (U, P): "stage,verifier",
    (U, N): "stage,finalizer",
    (U, B): "stage",
    (U, X): "stage,verifier",
    (N, D): "new",
    (N, P): "new",
    (N, N): "unchanged",
    (N, B): "new+failed",
    (N, X): "new",
    (B, D): "new+success",
    (B, P): "new+success",
    (B, N): "no",
    (B, B): "new",
    (B, X): "new+success",
    (P, D): "new",
    (P, P): "new",
    (P, N): "downgrade",
    (P, B): "no",
    (P, X): "no",
    (X, D): "new+results",
    (X, P): "new+results",
    (X, N): "no",
    (X, B): "no",
    (X, X): "new",
    (D, D): "new",
    (D, P): "downgrade",
    (D, N): "downgrade",
    (D, B): "no",
    (D, X): "no",
}


def expected(
    src: Status | None, dst: Status, actor: Actor, new: bool, failed: bool, ok: bool, res: bool
) -> str:
    """'ok', 'unchanged' or 'refused' per the oracle."""
    rule = ORACLE[(src, dst)]
    if rule == "no":
        return "refused"
    if actor is Actor.FINALIZER:
        return "ok" if (src, dst) == (U, N) else "refused"
    if actor in (Actor.JUDGE, Actor.GATE):
        return "ok" if rule == "downgrade" else "refused"
    if rule == "downgrade":
        return "refused"
    if src is U:
        return "ok" if actor.value in rule.split(",") else "refused"
    if src == dst and not new:
        return "unchanged"
    needs = {
        "new": new,
        "unchanged": not new,
        "new+failed": new and failed,
        "new+success": new and ok,
        "new+results": new and res,
    }
    return "ok" if needs[rule] else "refused"


OLD = Proof(call_refs=("a",))
MORE = Proof(call_refs=("a", "b"))


def test_oracle_covers_all_thirty_cells() -> None:
    assert len(ORACLE) == 30
    table = yaml.safe_load((CONFIG_DIR / "tracker_v1.yaml").read_text())["transitions"]
    assert sum(len(v) for v in table.values()) == 30


@pytest.mark.parametrize(("src", "dst"), list(ORACLE))
def test_every_cell_matches_the_oracle(src: Status | None, dst: Status) -> None:
    actors = (Actor.STAGE, Actor.VERIFIER, Actor.JUDGE, Actor.GATE, Actor.FINALIZER)
    for actor, new, failed, ok, res in itertools.product(actors, *([(False, True)] * 4)):
        current = None if src is None else OLD
        proposed = MORE if (new or src is None) else OLD
        reason = ReasonCode.SWEPT_FAILED if actor is Actor.FINALIZER else ReasonCode.DEADLINE
        try:
            result = transition_row(
                src, current, dst, proposed, actor, True, failed, ok, res, reason
            )
            got = "unchanged" if result.unchanged else "ok"
        except TrackerError:
            got = "refused"
        want = expected(src, dst, actor, new or src is None, failed, ok, res)
        assert got == want, (src, dst, actor, new, failed, ok, res)


def test_non_owner_stage_is_always_refused() -> None:
    for src, dst in ORACLE:
        with pytest.raises(TrackerError):
            transition_row(
                src, None if src is None else OLD, dst, MORE, Actor.STAGE, False, True, True, True
            )


def _call(i: int, ok: bool, results: int | None, kind: str = "RateLimited") -> CallRecord:
    return CallRecord(
        call_ref=f"c{i}",
        tool_category="web_search",
        success=ok,
        result_count=results if ok else None,
        error_kind=None if ok else kind,
    )


@pytest.mark.parametrize(
    ("expected_calls", "successes", "failures", "results", "cap"),
    list(itertools.product((1, 2, 3), (0, 1, 2, 3), (0, 1, 2), (0, 1), (False, True))),
)
def test_derive_truth_table(
    expected_calls: int, successes: int, failures: int, results: int, cap: bool
) -> None:
    calls = [_call(i, True, results) for i in range(successes)]
    calls += [_call(10 + i, False, None) for i in range(failures)]
    if not calls:
        with pytest.raises(ProofIncomplete):
            derive_outcome(expected_calls, calls, cap, True, None)
        assert derive_outcome(expected_calls, calls, cap, True, ReasonCode.DEADLINE).status is N
        return
    got = derive_outcome(expected_calls, calls, cap, True, None).status
    total = successes * results
    if successes == 0:
        want = B
    elif total == 0:
        want = X if (failures == 0 and successes >= expected_calls) else P
    else:
        want = D if (failures == 0 and successes >= expected_calls and not cap) else P
    assert got is want


def test_out_of_scope_needs_a_configured_reason() -> None:
    assert derive_outcome(1, [], False, False, ReasonCode.LANE_NOT_IN_SCOPE).status is X
    # waiting on the account owner is Not done (nobody searched), never None
    assert derive_outcome(1, [], False, False, ReasonCode.WAITING_ON_ACCOUNT_OWNER).status is N


_RANK = {B: 0, N: 0, P: 1, X: 2, D: 2}
calls_st = st.lists(
    st.builds(
        _call,
        st.integers(0, 50),
        st.booleans(),
        st.one_of(st.none(), st.integers(0, 3)),
    ),
    min_size=1,
    max_size=6,
    unique_by=lambda c: c.call_ref,
)


@given(calls_st, st.integers(1, 3), st.booleans())
def test_derive_is_order_independent_and_in_vocabulary(
    calls: list[CallRecord], expected_calls: int, cap: bool
) -> None:
    first = derive_outcome(expected_calls, calls, cap, True, None)
    assert first == derive_outcome(expected_calls, list(reversed(calls)), cap, True, None)
    assert first.status in set(Status)


@given(calls_st, st.integers(1, 3))
def test_adding_a_failure_never_moves_toward_done(calls: list[CallRecord], n: int) -> None:
    before = derive_outcome(n, calls, False, True, None).status
    after = derive_outcome(n, [*calls, _call(99, False, None)], False, True, None).status
    assert not (before is not D and after is D)


@given(calls_st, st.integers(1, 3))
def test_adding_a_success_with_results_never_leaves_done_or_partial(
    calls: list[CallRecord], n: int
) -> None:
    before = derive_outcome(n, calls, False, True, None).status
    after = derive_outcome(n, [*calls, _call(98, True, 2)], False, True, None).status
    if before in (D, P):
        assert after in (D, P)


@given(calls_st, st.integers(1, 3))
def test_unknown_count_never_supports_done_or_none_alone(calls: list[CallRecord], n: int) -> None:
    unknown = [c.model_copy(update={"result_count": None}) for c in calls if c.success]
    if unknown:
        assert derive_outcome(n, unknown, False, True, None).status not in (D, X)


def _row(key: str, prio: str, status: Status | None, swept: bool = False) -> RowState:
    return RowState(key, prio, status, None, "h" if status else None, swept)


@pytest.mark.parametrize(
    "policy", [("done", "none"), ("done", "none", "partial", "not_done", "blocked")]
)
@pytest.mark.parametrize("high", [None, D, P, N, B, X])
@pytest.mark.parametrize("low", [None, D, P, N, B, X])
@pytest.mark.parametrize("swept", [False, True])
def test_evaluate_every_combination(
    policy: tuple[str, ...], high: Status | None, low: Status | None, swept: bool
) -> None:
    rows = [_row("h", "high", high, swept and high is N), _row("m", "medium", low)]
    verdict = evaluate(rows, {"high_rows_allowed_for_completed": list(policy)}, 2)
    hard = high is None or low is None or (swept and high is N)
    gap = high is not None and high.value not in policy
    assert verdict.can_complete is (not hard and not gap)
    if verdict.can_complete:
        opened = {P, N, B}
        want = "complete_with_gaps" if (high in opened or low in opened) else "complete"
        assert verdict.level == want
    else:
        assert verdict.level == "cannot_complete"


def test_unseeded_or_short_tracker_cannot_complete() -> None:
    rows = [_row("h", "high", D)]
    assert not evaluate(rows, {}, None).can_complete
    assert not evaluate(rows, {}, 2).can_complete
    assert evaluate(rows, {}, 1).can_complete
    assert not evaluate([], {}, None).can_complete


def test_checklist_counts_are_read_not_typed() -> None:
    from account_intelligence.tracker.config import load_checklist

    rows = load_checklist().rows
    assert Counter(r.priority for r in rows) == {"high": 19, "medium": 7, "optional": 7}
    assert Counter(r.pass_no for r in rows) == {1: 5, 2: 28}


def test_transitions_doc_is_current_and_reason_texts_are_client_safe() -> None:
    import subprocess
    import sys
    from pathlib import Path

    from account_intelligence.safety.client_output import scan_text
    from account_intelligence.tracker.config import load_tracker_config

    root = Path(__file__).resolve().parents[2]
    check = subprocess.run([sys.executable, str(root / "scripts/gen_tracker_docs.py"), "--check"])
    assert check.returncode == 0, "docs/tracker/transitions.md is stale"
    texts = load_tracker_config().reason_codes
    assert set(texts) == {c.value for c in ReasonCode}
    assert not [code for code, text in texts.items() if scan_text(text)]


def test_runtime_denylist_is_the_skill_asset() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    skill = root / ".claude/skills/account-intelligence-platform/assets/client_output_denylist.txt"
    assert (CONFIG_DIR / "client_output_denylist.txt").read_text() == skill.read_text()
