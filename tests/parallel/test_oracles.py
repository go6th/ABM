"""Hand-written oracles on the fine-grained plan (synthetic accounts): a rich account, a sparse one
(no senior people found) and one whose people provider is down. Expected units, the dependencies
that matter, the outcome of every unit and the merge order are written out by hand; each account is
also run under 50 random completion orders (P1). A scale smoke runs 1,000 units and reports its
time and memory (measured, not asserted against numbers nobody supplied)."""

from __future__ import annotations

import asyncio
import random
import time
import tracemalloc
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

import pytest

from account_intelligence.workflow.state import append_unique, merge_maps, sum_counters
from parallel.config import load_parallel_config
from parallel.executor import BodyOutcome, Executor
from parallel.models import Durations, ExitReason, WorkUnit
from parallel.scheduler import Deadline
from parallel.units import fine_plan, unit_key
from tests.fakes.parallel import T0, ExecClock, FakeHooks, MemoryEventStore

CFG = load_parallel_config()
REDUCERS = {
    "completed": append_unique,
    "refs": merge_maps,
    "counters": sum_counters,
    "coverage_updates": merge_maps,
}
FAR = Deadline(T0 + timedelta(hours=4), 0)
LANES = ["salesforce", "sap"]
PEOPLE = {"senior_people": ["ann", "bob"], "top_people": ["ann"], "priority_a_people": ["ann"]}


def bodies(seed: int, failing: frozenset[str] = frozenset()) -> dict[str, Any]:
    rnd = random.Random(seed)

    async def body(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        await asyncio.sleep(rnd.random() / 500)
        if unit.unit_type in failing:
            raise RuntimeError("provider down")  # a typed failure, never a sibling cancel
        return BodyOutcome(
            {"completed": [unit.key], "refs": {unit.key: "ok"}, "counters": {"n": 1}}
        )

    return {t: body for t in CFG.unit_types} | {"expand": body}


async def run(plan_units: tuple[WorkUnit, ...], seed: int, failing: frozenset[str] = frozenset()):
    store, hooks = MemoryEventStore(), FakeHooks()
    ex = Executor(
        store,
        bodies(seed, failing),
        hooks,
        ExecClock(),
        REDUCERS,
        max_in_flight=6,
        unit_attempts=3,
        stall_wait_s=1.0,
    )
    result = await ex.run_phase(plan_units, {}, FAR, Durations({}, 60.0))
    outcome = {u.key: store.kinds(u.key)[-1] for u in plan_units}
    return result, outcome, hooks


def test_rich_account_oracle() -> None:
    plan = fine_plan(CFG, lanes_in_scope=LANES, stored=PEOPLE)
    keys = {u.key for u in plan.units}
    expected = {
        unit_key("jobs_board", "salesforce"),
        unit_key("jobs_board", "sap"),
        unit_key("title_search", "salesforce", "1"),
        unit_key("title_search", "salesforce", "2"),
        unit_key("title_search", "sap", "1"),
        unit_key("title_search", "sap", "2"),
        unit_key("profile_pull", None, "ann"),
        unit_key("profile_pull", None, "bob"),
        unit_key("posts_priority_a", None, "ann"),
        *(unit_key("name_search", None, f"ann:{c}") for c in CFG.name_search_categories),
        *(
            unit_key(t)
            for t in (
                "pass1_relationship",
                "baseline",
                "news_pull",
                "postings_12m",
                "tech_detections",
                "review_communities",
                "people_merge",
                "association_search",
                "people_levels",
                "platform_sentences",
                "tech_cross_check",
                "lead_verification",
            )
        ),
    }
    assert keys == expected and len(keys) == 26
    by = plan.by_key()
    assert by[unit_key("people_merge")].depends_on == tuple(
        sorted(unit_key("title_search", lane, p) for lane in LANES for p in ("1", "2"))
    )
    assert by[unit_key("posts_priority_a", None, "ann")].depends_on == (unit_key("people_levels"),)
    outcomes = []
    for seed in range(50):
        result, outcome, _ = asyncio.run(run(plan.units, seed))
        assert result.exit is ExitReason.CONVERGED
        assert set(outcome.values()) == {"finished"}
        outcomes.append(result.update)
    assert all(o == outcomes[0] for o in outcomes)  # 50 random completion orders, one answer
    assert outcomes[0]["completed"] == sorted(expected)  # merged in key order
    assert outcomes[0]["counters"] == {"n": 26}


def test_sparse_account_oracle_has_no_person_units_and_still_verifies() -> None:
    plan = fine_plan(
        CFG,
        lanes_in_scope=["sap"],
        stored={"senior_people": [], "top_people": [], "priority_a_people": []},
    )
    types = {u.unit_type for u in plan.units}
    assert "profile_pull" not in types and "name_search" not in types
    assert "posts_priority_a" not in types
    result, outcome, _ = asyncio.run(run(plan.units, 1))
    assert result.exit is ExitReason.CONVERGED and set(outcome.values()) == {"finished"}
    assert outcome[unit_key("lead_verification")] == "finished"


def test_people_provider_outage_oracle() -> None:
    plan = fine_plan(CFG, lanes_in_scope=LANES, stored=PEOPLE)
    for seed in range(10):
        result, outcome, hooks = asyncio.run(run(plan.units, seed, frozenset({"title_search"})))
        assert result.exit is ExitReason.CONVERGED
        expected_blocked = {
            unit_key("people_merge"),
            unit_key("association_search"),
            unit_key("people_levels"),
            unit_key("profile_pull", None, "ann"),
            unit_key("profile_pull", None, "bob"),
            unit_key("posts_priority_a", None, "ann"),
            *(unit_key("name_search", None, f"ann:{c}") for c in CFG.name_search_categories),
        }
        failed = {k for k, v in outcome.items() if v == "failed_terminal"}
        blocked = {k for k, v in outcome.items() if v == "blocked"}
        assert failed == {unit_key("title_search", lane, p) for lane in LANES for p in "12"}
        assert blocked == expected_blocked
        assert {k for k, r in hooks.unrun if r == "dependency_failed"} == expected_blocked
        # everything that does not need the people list still ran, verification included (soft)
        assert outcome[unit_key("tech_cross_check")] == "finished"
        assert outcome[unit_key("lead_verification")] == "finished"
        assert result.update["references"]["parallel_incomplete"] == "units_blocked_or_failed"


@pytest.mark.asyncio
async def test_scale_smoke_1000_units_reports_time_and_memory(capsys: pytest.CaptureFixture[str]):
    units = tuple(
        WorkUnit(f"u{i:04d}", "t", "t", depends_on=(f"u{i // 2:04d}",) if i else ())
        for i in range(1000)
    )

    async def body(unit: WorkUnit, state: Mapping[str, Any]) -> BodyOutcome:
        return BodyOutcome({"counters": {"n": 1}})

    store = MemoryEventStore()
    ex = Executor(
        store,
        {"t": body},
        FakeHooks(),
        ExecClock(),
        REDUCERS,
        max_in_flight=8,
        unit_attempts=3,
        stall_wait_s=1.0,
    )
    tracemalloc.start()
    started = time.perf_counter()
    result = await ex.run_phase(units, {}, FAR, Durations({}, 1.0))
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.exit is ExitReason.CONVERGED and result.update["counters"] == {"n": 1000}
    with capsys.disabled():
        print(
            f"\nscale smoke: 1000 units, {len(store.events)} events, {elapsed:.2f} s, "
            f"peak traced memory {peak / 1e6:.1f} MB"
        )
