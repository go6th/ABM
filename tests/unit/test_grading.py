"""Grading facade (agents/grading.py) unit tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.agents.grading import (
    Evidence,
    confidence,
    grade_attribute,
    load_config,
    recency,
)
from account_intelligence.agents.grading import (
    grade as engine_grade,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)
WEIGHTS, HALF_LIVES = load_config()


def evidence(tier: str, group: str, value: str, age_days: float = 0) -> Evidence:
    return Evidence(tier, group, NOW - timedelta(days=age_days), value)


def grade(items: list[Evidence], attr_class: str = "leadership") -> dict:
    return grade_attribute(items, attr_class, NOW, WEIGHTS, HALF_LIVES)


def test_fresh_t1_and_t2_independent_sources_are_grade_a() -> None:
    result = grade([evidence("T1", "site", "x"), evidence("T2", "broker", "x")])
    assert result["confidence"] == 0.975
    assert result["grade"] == "A"


def test_aggregators_with_shared_upstream_count_once() -> None:
    result = grade([evidence("T4", "upstream", "x"), evidence("T4", "upstream", "x")])
    assert result["confidence"] == 0.4
    assert result["grade"] == "D"


def test_hiring_evidence_decays_at_configured_half_life() -> None:
    result = grade([evidence("T1", "site", "x", 21)], "hiring")
    assert result["confidence"] == 0.45


def test_single_primary_source_cannot_receive_grade_a() -> None:
    result = grade([evidence("T1", "site", "x")], "funding")
    assert result["confidence"] == 0.9
    assert result["grade"] == "B"


def test_t3_only_independent_sources_cannot_receive_grade_a() -> None:
    result = grade([evidence("T3", "one", "x"), evidence("T3", "two", "x")], "funding")
    assert result["confidence"] == 0.91
    assert result["grade"] == "B"


def test_independent_conflicting_values_are_flagged() -> None:
    result = grade([evidence("T1", "site", "x"), evidence("T2", "curated", "y")])
    assert result["status"] == "conflicting"


def test_weak_competing_value_does_not_create_conflict() -> None:
    result = grade([evidence("T1", "site", "x"), evidence("T4", "aggregator", "y")])
    assert result["status"] == "graded"
    assert result["value"] == "x"


def test_one_lineage_uses_maximum_strength() -> None:
    result = grade([evidence("T2", "same", "x"), evidence("T4", "same", "x")], "technology")
    assert result["confidence"] == 0.75


def test_absent_evidence_is_reported_for_hypothesis_handling() -> None:
    assert grade([])["status"] == "no_evidence"


def test_recency_is_one_at_present_and_monotone_with_age() -> None:
    assert recency(0, 10) == 1
    assert recency(10, 10) == 0.5
    assert recency(20, 10) < recency(10, 10)


def test_future_evidence_is_treated_as_fresh() -> None:
    assert recency(-5, 10) == 1


def test_nonpositive_half_life_is_rejected() -> None:
    with pytest.raises(ValueError, match="half_life_days must be positive"):
        recency(1, 0)


def test_grade_c_threshold() -> None:
    assert engine_grade(0.6, {"one": (0.6, "T1")}) == "C"


def test_competing_values_sharing_lineage_are_not_conflicting() -> None:
    result = grade(
        [
            evidence("T1", "shared", "x"),
            evidence("T1", "independent-x", "x"),
            evidence("T1", "shared", "y"),
            evidence("T1", "independent-y", "y"),
        ]
    )
    assert result["status"] == "graded"


def test_config_can_be_loaded_from_an_explicit_path() -> None:
    weights, half_lives = load_config(Path("config/grading.yaml"))
    assert weights["T1"] == 0.9
    assert half_lives["hiring"] == 21


def test_default_weights_load_when_half_lives_are_supplied() -> None:
    result = grade_attribute(
        [evidence("T1", "site", "x")],
        "leadership",
        NOW,
        weights=None,
        half_lives=HALF_LIVES,
    )
    assert result["confidence"] == 0.9


def test_default_half_lives_load_when_weights_are_supplied() -> None:
    result = grade_attribute(
        [evidence("T1", "site", "x")],
        "leadership",
        NOW,
        weights=WEIGHTS,
        half_lives=None,
    )
    assert result["confidence"] == 0.9


@given(st.lists(st.tuples(st.sampled_from(["T1", "T2", "T3", "T4"]), st.text(max_size=8))))
def test_confidence_stays_in_unit_interval(items: list[tuple[str, str]]) -> None:
    strengths = {f"{index}": (WEIGHTS[tier], tier) for index, (tier, _) in enumerate(items)}
    result = confidence(strengths)
    assert 0 <= result <= 1


@given(
    st.sampled_from(["T1", "T2", "T3", "T4"]),
    st.sampled_from(["T1", "T2", "T3", "T4"]),
)
def test_adding_an_independent_source_never_lowers_confidence(
    first_tier: str, second_tier: str
) -> None:
    first = confidence({"one": (WEIGHTS[first_tier], first_tier)})
    with_second = confidence(
        {
            "one": (WEIGHTS[first_tier], first_tier),
            "two": (WEIGHTS[second_tier], second_tier),
        }
    )
    assert with_second >= first


def test_confidence_of_no_groups_is_zero() -> None:
    assert confidence({}) == 0


def test_grade_is_monotone_for_increasing_confidence() -> None:
    strengths = {"one": (0.9, "T1"), "two": (0.75, "T2")}
    order = {"D": 0, "C": 1, "B": 2, "A": 3}
    assert order[engine_grade(0.5, strengths)] <= order[engine_grade(0.9, strengths)]
