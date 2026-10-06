"""Unit planning (P3): caps, placeholders, stable keys, model suggestions refused."""

from __future__ import annotations

import pytest

from parallel.config import load_parallel_config
from parallel.errors import FanOutCapExceeded, UnitNotAllowed
from parallel.units import (
    EXPAND,
    accept_suggestion,
    body_plan,
    fine_plan,
    placeholder_key,
    unit_key,
)

CFG = load_parallel_config()


def test_static_plan_fans_out_per_lane_and_pass_and_waits_for_people() -> None:
    plan = fine_plan(CFG, lanes_in_scope=["salesforce", "sap"])
    keys = set(plan.by_key())
    assert unit_key("jobs_board", "salesforce") in keys and unit_key("jobs_board", "sap") in keys
    assert unit_key("jobs_board", "servicenow") not in keys  # not in scope for this run
    assert {unit_key("title_search", "sap", p) for p in ("1", "2")} <= keys
    assert unit_key("postings_12m") in keys  # pulled once
    # dynamic sets are not known before the people merge: one placeholder each
    for t in ("profile_pull", "posts_priority_a", "name_search"):
        u = plan.by_key()[placeholder_key(t)]
        assert u.body == EXPAND and not u.paid
    assert plan.by_key()[placeholder_key("name_search")].depends_on == (unit_key("people_merge"),)


def test_units_never_exceed_their_caps() -> None:
    people = [f"p{i:03d}" for i in range(200)]
    plan = fine_plan(
        CFG, stored={"senior_people": people, "priority_a_people": people, "top_people": people}
    )
    by_type: dict[str, int] = {}
    for u in plan.units:
        by_type[u.unit_type] = by_type.get(u.unit_type, 0) + 1
    assert by_type["profile_pull"] == CFG.dynamic_caps["senior_people"]
    assert by_type["posts_priority_a"] == CFG.dynamic_caps["priority_a_people"]
    top = CFG.dynamic_caps["top_people"]
    assert by_type["name_search"] == top * len(CFG.name_search_categories)
    assert plan.truncated == {  # never silent: what was dropped is reported
        "senior_people": 200 - CFG.dynamic_caps["senior_people"],
        "priority_a_people": 200 - CFG.dynamic_caps["priority_a_people"],
        "top_people_x_category": 200 - top,
    }


def test_the_run_total_is_capped() -> None:
    from dataclasses import replace

    small = replace(CFG, dynamic_caps={**CFG.dynamic_caps, "units_per_run": 5})
    with pytest.raises(FanOutCapExceeded):
        fine_plan(small)


def test_unit_keys_are_stable_across_rebuilds() -> None:
    before = fine_plan(CFG).by_key()
    after = fine_plan(CFG, stored={"senior_people": ["b", "a", "a"], "top_people": ["a"]}).by_key()
    replaced = {placeholder_key("profile_pull"), placeholder_key("name_search")}
    expansions = {k for k in after if k.startswith(("profile_pull|", "name_search|"))}
    for key, unit in before.items():
        if key in replaced:
            assert key not in after  # the placeholder is replaced by its expansion
            continue
        old = set(unit.depends_on)
        new = set(after[key].depends_on)
        # existing dependencies are kept; a replaced placeholder is swapped for its expansion
        assert old - replaced <= new
        assert new - old <= expansions
        if old & replaced:
            assert new & expansions
    assert (
        unit_key("profile_pull", None, "a") in after
        and unit_key("profile_pull", None, "b") in after
    )
    assert after[unit_key("people_levels")].depends_on == tuple(
        sorted(
            [
                unit_key("association_search"),
                unit_key("profile_pull", None, "a"),
                unit_key("profile_pull", None, "b"),
            ]
        )
    )


def test_a_model_suggested_unit_outside_the_plan_is_refused() -> None:
    plan = fine_plan(CFG, stored={"senior_people": ["a"], "top_people": ["a"]})
    assert accept_suggestion(plan, "profile_pull", None, "a") == unit_key("profile_pull", None, "a")
    with pytest.raises(UnitNotAllowed):
        accept_suggestion(plan, "profile_pull", None, "someone-the-model-invented")
    with pytest.raises(UnitNotAllowed):
        accept_suggestion(plan, "delete_everything", None, None)


def test_body_plan_runs_the_coarsened_graph_and_chains_source_allocators() -> None:
    plan = body_plan(CFG, high=frozenset({"people"}), est_cost_micros={"news": 7})
    units = plan.by_key()
    assert units["body|people"].priority == 1 and units["body|news"].priority == 0
    assert units["body|news"].est_cost_micros == 7 and units["body|jobs"].est_cost_micros is None
    assert units["body|tech"].depends_on == ("body|jobs",)
    assert units["body|people"].routes == ("people_by_title", "profile_enrichment")
    assert units["body|people"].slot_route is None  # composite: the per-call governor decides
    assert units["body|news"].slot_route == "structured_signals"


def test_two_allocating_bodies_are_chained_in_key_order() -> None:
    from dataclasses import replace

    bodies = dict(CFG.bodies)
    bodies["baseline"] = replace(bodies["baseline"], allocates_sources=True)
    plan = body_plan(replace(CFG, bodies=bodies)).by_key()
    assert "body|baseline" in plan["body|people"].depends_on
