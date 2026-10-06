"""Merge by key (P1), critical path (P13) and the cancellation token (P9)."""

from __future__ import annotations

import asyncio
import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.workflow.state import append_unique, merge_maps, sum_counters
from parallel.cancel import CancelToken
from parallel.critical_path import analyse, longest_path
from parallel.errors import Cancelled, MergeConflict
from parallel.merge import allocate_in_key_order, merge_in_key_order
from parallel.models import EventKind, UnitEvent, WorkUnit

REDUCERS = {
    "completed_stages": append_unique,
    "references": merge_maps,
    "coverage_updates": merge_maps,
    "counters": sum_counters,
    "loop_exits": merge_maps,
}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _results(n: int) -> dict[str, dict]:
    return {
        f"body|u{i}": {
            "completed_stages": [f"u{i}"],
            "references": {f"proposals:u{i}": f"ref{i}"},
            "counters": {"calls": i},
            "coverage_updates": {f"row{i}": {"status": "done"}},
        }
        for i in range(n)
    }


@given(st.integers(1, 8), st.randoms(use_true_random=False))
def test_merge_is_independent_of_arrival_order(n: int, rnd: random.Random) -> None:
    results = _results(n)
    items = list(results.items())
    rnd.shuffle(items)
    assert merge_in_key_order(dict(items), REDUCERS) == merge_in_key_order(results, REDUCERS)
    merged = merge_in_key_order(dict(items), REDUCERS)
    assert merged["completed_stages"] == [f"u{i}" for i in range(n)]
    assert merged["counters"] == {"calls": sum(range(n))}


def test_merge_refuses_conflicting_writes() -> None:
    with pytest.raises(MergeConflict):
        merge_in_key_order({"a": {"x": 1}, "b": {"x": 2}}, {})
    with pytest.raises(MergeConflict):
        merge_in_key_order({"a": {"references": {"k": 1}}, "b": {"references": {"k": 2}}}, REDUCERS)
    assert merge_in_key_order({"a": {"x": 1}, "b": {"x": 1}}, {}) == {"x": 1}
    assert merge_in_key_order({"a": {"x": 1}}, {}, base={"y": 2}) == {"x": 1, "y": 2}


@given(
    st.lists(st.lists(st.sampled_from([f"https://s{i}.example" for i in range(6)])), max_size=6),
    st.randoms(use_true_random=False),
)
def test_source_codes_are_independent_of_completion_order(
    cited: list[list[str]], rnd: random.Random
) -> None:
    requests = {f"u{i}": urls for i, urls in enumerate(cited)}

    async def codes(order: list[tuple[str, list[str]]]) -> dict[str, str]:
        counter = itertools.count(1)

        async def allocate(url: str) -> str:
            await asyncio.sleep(0)
            return f"S-{next(counter):04d}"

        return await allocate_in_key_order(dict(order), allocate)

    items = list(requests.items())
    shuffled = items[:]
    rnd.shuffle(shuffled)
    assert asyncio.run(codes(items)) == asyncio.run(codes(shuffled))


@st.composite
def weighted_dags(draw: st.DrawFn) -> tuple[dict[str, list[str]], dict[str, float]]:
    n = draw(st.integers(1, 7))
    names = [f"n{i}" for i in range(n)]
    deps = {
        name: draw(st.lists(st.sampled_from(names[:i]), unique=True)) if i else []
        for i, name in enumerate(names)
    }
    weights = {name: float(draw(st.integers(0, 20))) for name in names}
    return deps, weights


def _all_paths(deps: dict[str, list[str]], node: str) -> list[list[str]]:
    if not deps[node]:
        return [[node]]
    return [p + [node] for d in deps[node] for p in _all_paths(deps, d)] + [[node]]


@given(weighted_dags())
def test_critical_path_equals_the_longest_path(case: tuple[dict, dict]) -> None:
    deps, weights = case
    length, path = longest_path(deps, weights)
    brute = max(sum(weights[n] for n in p) for node in deps for p in _all_paths(deps, node))
    assert length == brute
    assert sum(weights[n] for n in path) == length
    for a, b in zip(path, path[1:], strict=False):
        assert a in deps[b]
    assert longest_path(deps, weights) == (length, path)  # deterministic


def test_analyse_reads_waits_runs_and_idle_from_events() -> None:
    a = WorkUnit("a", "t", "b")
    b = WorkUnit("b", "t", "b", depends_on=("a",))
    ev = [
        UnitEvent("a", EventKind.QUEUED, T0),
        UnitEvent("b", EventKind.QUEUED, T0),
        UnitEvent("a", EventKind.STARTED, T0 + timedelta(seconds=5), lease_wait_s=2),
        UnitEvent("a", EventKind.FINISHED, T0 + timedelta(seconds=15)),
        UnitEvent("b", EventKind.STARTED, T0 + timedelta(seconds=20)),
        UnitEvent("b", EventKind.FINISHED, T0 + timedelta(seconds=30)),
    ]
    report = analyse([a, b], ev)
    assert report.makespan_s == 30
    assert report.critical_path == ("a", "b") and report.critical_path_s == 20
    assert report.idle_s == 10
    ta, tb = report.timings
    assert (ta.queue_wait_s, ta.run_s, ta.lease_wait_s) == (5, 10, 2)
    assert (tb.queue_wait_s, tb.outcome) == (20, "finished")
    assert analyse([], []).makespan_s == 0


def test_cancel_token_first_reason_wins() -> None:
    token = CancelToken()
    token.raise_if_set()
    assert not token.is_set() and token.reason is None
    token.cancel("slice_deadline")
    token.cancel("run_cancelled")
    assert token.reason == "slice_deadline"
    with pytest.raises(Cancelled) as info:
        token.raise_if_set()
    assert info.value.reason == "slice_deadline"
    assert asyncio.run(token.wait()) == "slice_deadline"
