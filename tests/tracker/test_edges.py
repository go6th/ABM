"""Gap loop (spec 9.9), config failure modes, dropped-claim downgrades and remaining edges."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.config import load_checklist, load_tracker_config
from account_intelligence.tracker.derive import Outcome
from account_intelligence.tracker.errors import ConfigError, IllegalTransition, NotOwner
from account_intelligence.tracker.gap import (
    ExitReason,
    TrackerRowState,
    all_high_converged,
    gap_pass_result,
    open_rows,
)
from account_intelligence.tracker.machine import transition_row
from account_intelligence.tracker.model import Actor, Proof, ProofCall, ReasonCode, Status

D, P, N, B, X = Status.DONE, Status.PARTIAL, Status.NOT_DONE, Status.BLOCKED, Status.NONE


def _row(key: str, prio: str, status: Status | None, calls: int = 0, attempts: int = 0):  # type: ignore[no-untyped-def]
    return TrackerRowState(
        key, prio, status, None, "h", False, proof_calls=calls, attempts=attempts
    )


def test_open_rows_order_and_exhaustion() -> None:
    keys = [r.key for r in load_checklist().rows]
    rows = [
        _row(keys[30], "optional", N),
        _row(keys[0], "high", None),
        _row(keys[1], "high", B, attempts=3),  # exhausted: not retried
        _row(keys[15], "medium", P),
        _row(keys[2], "high", D),
    ]
    assert [r.key for r in open_rows(rows)] == [keys[0], keys[15], keys[30]]
    assert [r.key for r in open_rows(rows, cap=10)] == [keys[0], keys[1], keys[15], keys[30]]


def test_progress_needs_a_better_status_or_strictly_more_calls() -> None:
    before = [_row("a", "high", B, calls=1), _row("b", "high", P, calls=1)]
    assert gap_pass_result(before, [_row("a", "high", P, calls=2), before[1]])
    assert gap_pass_result(before, [before[0], _row("b", "high", P, calls=2)])
    # a different hash with the same number of calls is not progress (audit F-12)
    assert not gap_pass_result(before, [_row("a", "high", B, calls=1), before[1]])
    assert not gap_pass_result(before, [_row("z", "high", D)])
    assert not gap_pass_result(before, [_row("a", "high", N, calls=1)])  # moved down
    assert all_high_converged([_row("a", "high", D), _row("b", "medium", P)])
    assert not all_high_converged([_row("a", "high", P)])
    assert ExitReason("no_progress") is ExitReason.NO_PROGRESS


@given(st.lists(st.sampled_from([None, N, B, P, X, D]), min_size=1, max_size=40))
def test_a_gap_loop_driven_by_any_outcome_sequence_terminates_within_the_cap(
    outcomes: list[Status | None],
) -> None:
    """Simulate the planner loop: at most 2 re-passes, stop on no progress or convergence."""
    rows = [_row("a", "high", None)]
    passes, feed = 0, iter(outcomes)
    while passes < 2 and open_rows(rows) and not all_high_converged(rows):
        after = []
        for row in rows:
            status = next(feed, row.status)
            after.append(_row(row.key, "high", status, row.proof_calls + 1, row.attempts + 1))
        progressed = gap_pass_result(rows, after)
        rows, passes = after, passes + 1
        if not progressed:
            break
    assert passes <= 2


def test_outcome_value_semantics() -> None:
    assert Outcome(D) == Outcome(D) and Outcome(D) != Outcome(P)
    assert Outcome(D).__eq__("done") is NotImplemented
    assert len({Outcome(D), Outcome(D)}) == 1
    assert repr(Outcome(P, ReasonCode.CAP_REACHED)) == "Outcome(partial, cap_reached)"


def test_machine_finalizer_needs_a_sweep_reason() -> None:
    with pytest.raises(IllegalTransition):
        transition_row(
            None, None, N, Proof(), Actor.FINALIZER, False, False, False, False, ReasonCode.DEADLINE
        )
    with pytest.raises(NotOwner):
        transition_row(
            None,
            None,
            D,
            Proof(),
            Actor.FINALIZER,
            False,
            False,
            False,
            False,
            ReasonCode.SWEPT_FAILED,
        )
    with pytest.raises(IllegalTransition):  # a downgrade needs a reason and an existing proof
        transition_row(
            D, Proof(call_refs=("a",)), P, Proof(), Actor.JUDGE, False, False, False, False, None
        )


def test_proof_calls_must_match_refs_and_hash_ignores_notes() -> None:
    call = ProofCall(call_ref="a", tool_category="web_search", success=True, result_count=1)
    with pytest.raises(ValueError, match="match"):
        Proof(call_refs=("b",), calls=(call,))
    proof = Proof(call_refs=("a",), calls=(call,))
    assert proof.with_note("downgraded").proof_hash == proof.proof_hash


def test_completeness_text_names_its_base() -> None:
    rows = [RowState("a", "high", D, None, "h", False), RowState("b", "high", P, None, "h", False)]
    assert evaluate(rows, {}, 2).completeness_text == "1 of 2 High rows Done or None"
    assert not evaluate(
        [RowState("a", "high", D, None, None, False)], {}, 1
    ).can_complete  # a status without proof


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


def test_config_fails_fast_on_bad_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_tracker_config(tmp_path / "missing.yaml")
    with pytest.raises(ConfigError):
        load_tracker_config(_write(tmp_path, "bad.yaml", "version: 1\n"))
    good = Path(__file__).resolve().parents[2] / "config/tracker_v1.yaml"
    text = good.read_text()
    with pytest.raises(ConfigError, match="reason codes"):
        load_tracker_config(
            _write(tmp_path, "codes.yaml", text.replace("  page_cap: ", "  page_cap_x: "))
        )
    with pytest.raises(ConfigError, match="unknown status"):
        load_tracker_config(
            _write(tmp_path, "table.yaml", text.replace("  unset:\n", "  pending:\n", 1))
        )
    with pytest.raises(ConfigError):
        load_checklist(_write(tmp_path, "c1.yaml", "version: 1\nrows: [{key: a}]\n"))
    with pytest.raises(ConfigError, match="unique"):
        load_checklist(
            _write(
                tmp_path,
                "c2.yaml",
                "version: 1\nrows:\n  - {key: a, pass: 1, priority: High, row: x}\n"
                "  - {key: a, pass: 1, priority: High, row: y}\n",
            )
        )
    with pytest.raises(ConfigError):
        load_checklist(
            _write(tmp_path, "c3.yaml", "version: 1\nrows: [{key: a, pass: 1, priority: urgent}]\n")
        )
    assert load_checklist().row("no_such_row") is None


class _Conflicting:
    """Wraps the in-memory repo and raises ``VersionConflict`` on the first ``fail`` updates."""

    def __init__(self, fail: int) -> None:
        from tests.fakes.tracker_ports import InMemoryCoverageRepo

        self.inner, self.fail = InMemoryCoverageRepo(), fail

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self.inner, name)

    async def update_row(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail:
            self.fail -= 1
            raise self.inner.VersionConflict("stale")
        return await self.inner.update_row(*args, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [1, 2, 3])
async def test_version_conflicts_are_re_read_at_most_three_times(fail: int) -> None:
    from uuid import uuid4

    from account_intelligence.tracker.errors import VersionConflict
    from account_intelligence.tracker.model import CallRecord
    from account_intelligence.tracker.service import TrackerService
    from account_intelligence.workflow.spec import load_spec
    from tests.fakes.tracker_ports import FakeCallLedger, FakeRunInput

    tenant, run = uuid4(), uuid4()
    ledger = FakeCallLedger()
    ref = ledger.add(
        tenant,
        run,
        CallRecord(call_ref="c", tool_category="web_search", success=True, result_count=1),
    )
    repo = _Conflicting(0)
    service = TrackerService(repo, ledger, FakeRunInput(), owners=load_spec().coverage_owners)  # type: ignore[arg-type]
    await service.seed(run, tenant)
    calls = [
        lambda: service.record_outcome(
            tenant, run, "company_news", [ref], Actor.STAGE, stage="news"
        ),
        lambda: service.downgrade(
            tenant, run, "company_news", P, Actor.GATE, ReasonCode.CLAIMS_DROPPED
        ),
        lambda: service.sweep(run, tenant, ReasonCode.SWEPT_FAILED),
    ]
    for call in calls:
        repo.fail = fail
        if fail < 3:
            await call()
        elif call is calls[2]:
            assert "company_news" not in await call()  # the sweep gives up on that row only
        else:
            with pytest.raises(VersionConflict):
                await call()
            repo.fail = 0
            await call()
