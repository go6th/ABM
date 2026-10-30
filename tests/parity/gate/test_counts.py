"""The count module mirrors the workbook formulas."""

from __future__ import annotations

from typing import Any

from parity import counts
from tests.parity.gate.conftest import OTHER_ENTITY, PEOPLE_LANES, PREFIX


def test_jobs_summary_counts_account_rows_naming_the_lane(bundle: dict[str, Any]) -> None:
    summary = counts.jobs_summary(bundle)
    assert summary["Salesforce"] == counts.JobsCount(3, 1, 2, 1, 1, 0, 1)
    # the divested row names SAP but its entity does not start with the prefix
    assert summary["SAP"] == counts.JobsCount(3, 1, 2, 1, 0, 1, 1)


def test_jobs_summary_matches_without_case_like_countifs(bundle: dict[str, Any]) -> None:
    bundle["jobs_rows"][0]["entity"] = PREFIX.upper()
    bundle["jobs_rows"][0]["platforms"] = ["SALESFORCE"]
    assert counts.jobs_summary(bundle)["Salesforce"].total == 3


def test_row_totals(bundle: dict[str, Any]) -> None:
    assert counts.jobs_row_totals(bundle) == counts.RowTotals(6, 5, 1)
    assert OTHER_ENTITY not in [
        r["entity"] for r in bundle["jobs_rows"] if counts.is_account_row(r, PREFIX)
    ]


def test_people_levels_and_seniority(bundle: dict[str, Any]) -> None:
    levels = counts.people_level_counts(bundle)
    assert levels["C-level, EVP and SVP"] == counts.LevelCount(1, 1)
    assert levels["VP"] == counts.LevelCount(1, 1)
    assert levels["Director"] == counts.LevelCount(1, 0)
    assert levels["Lead, owner, specialist or developer"] == counts.LevelCount(1, 0)
    assert counts.people_level_totals(bundle) == counts.LevelCount(5, 2)
    assert counts.meets_seniority(bundle) == 4


def test_people_lanes_count_senior_people_only(bundle: dict[str, Any]) -> None:
    lanes = counts.people_lane_counts(bundle)
    # the developer at level 9 is in the Salesforce lane but is not senior
    assert lanes[PEOPLE_LANES[0]] == counts.LaneCount(1, 0, 1, 2)
    assert lanes[PEOPLE_LANES[1]] == counts.LaneCount(0, 1, 0, 1)
    assert lanes[PEOPLE_LANES[2]] == counts.LaneCount(1, 0, 0, 1)
    assert counts.people_lane_totals(bundle) == counts.LaneCount(2, 1, 1, 4)


def test_people_lanes_fall_back_to_the_rows(bundle: dict[str, Any]) -> None:
    bundle["meta"]["people_lanes"] = []
    assert counts.people_lanes(bundle) == [PEOPLE_LANES[2], PEOPLE_LANES[0], PEOPLE_LANES[1]]


def test_rank_rule() -> None:
    assert counts.rank_of(40, 40 / 60) == "A"
    assert counts.rank_of(6, 6 / 60) == "B"
    assert counts.rank_of(5, 5 / 60) == "B"
    assert counts.rank_of(2, 2 / 60) == "C"
    assert counts.rank_of(1, 1 / 60) == ""
    assert counts.rank_of(4, 0.2) == "B"  # share enough for A, count below 5
    assert counts.rank_of(2, 0.2) == "C"  # share enough for B, count below 3
    assert counts.share_of(3, 0) == 0.0


def test_platform_ranking_and_counts(bundle: dict[str, Any]) -> None:
    rows = counts.platform_ranking(bundle)
    assert [(r.platform, r.rank) for r in rows] == [
        ("SAP", "A"),
        ("Salesforce", "A"),
        ("Workday", "B"),
        ("Qlik", "C"),
        ("Boomi", ""),
    ]
    assert counts.rank_counts(bundle) == {"A": 2, "B": 1, "C": 1}


def test_summary_values_are_flat_and_json_friendly(bundle: dict[str, Any]) -> None:
    values = counts.summary_values(bundle)
    assert values["jobs.Salesforce.total"] == 3
    assert values["jobs.rows.other"] == 1
    assert values["people.senior"] == 4
    assert values[f"people.lane.{PEOPLE_LANES[0]}.total"] == 2
    assert values["ranking.SAP.rank"] == "A"
    assert values["ranking.count.B"] == 1
    assert all(isinstance(v, int | float | str) for v in values.values())
