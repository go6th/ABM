"""Drift tests: every enum value must match the YAML config it came from.

The database CHECK constraints, the Python enums and the operator-facing YAML
config must agree; a config edit without a migration (or the reverse) fails
here before it reaches a run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.db.enums import (
    CoverageStatus,
    Grade,
    Lane,
    PersonLevel,
    Priority,
    all_enums,
    normalize_key,
    values,
)

CONFIG = Path("config")


def _load(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text())


def test_grade_matches_product_grades_in_grading_yaml() -> None:
    grading = _load("grading.yaml")
    configured = set(grading["product_grades"])
    assert set(values(Grade)) == configured - {"internal"}
    assert "internal" in configured  # Phase B vocabulary stays in the config, not in v1


def test_every_grade_requires_confirmation_rule_stays_in_config() -> None:
    grading = _load("grading.yaml")
    lead = grading["product_grades"]["lead"]
    assert lead["stays_lead_only_if"]["min_distinct_queries"] == 3
    assert lead["stays_lead_only_if"]["min_distinct_tool_categories"] == 2


def test_coverage_status_matches_checklist_statuses() -> None:
    checklist = _load("checklist.yaml")
    configured = {normalize_key(s) for s in checklist["statuses"]}
    assert set(values(CoverageStatus)) == configured


def test_priority_matches_priorities_used_by_checklist_rows() -> None:
    checklist = _load("checklist.yaml")
    configured = {normalize_key(row["priority"]) for row in checklist["rows"]}
    assert set(values(Priority)) == configured


def test_lane_matches_lanes_yaml_ids() -> None:
    lanes = _load("lanes.yaml")
    assert set(values(Lane)) == {lane["id"] for lane in lanes["lanes"]}


def test_person_level_matches_people_scope_levels() -> None:
    people_scope = _load("people_scope.yaml")
    configured = {normalize_key(level) for level in people_scope["levels_in_scope"]}
    assert set(values(PersonLevel)) == configured


def test_all_vocabulary_values_are_unique_lowercase_snake_case() -> None:
    for enum_cls in all_enums():
        stored = values(enum_cls)  # type: ignore[arg-type]
        assert len(stored) == len(set(stored)), enum_cls.__name__
        assert stored == tuple(stored), enum_cls.__name__
        for value in stored:
            assert value == normalize_key(value), (enum_cls.__name__, value)


def test_enum_members_accept_plain_strings() -> None:
    assert Grade("confirmed") is Grade.CONFIRMED
    assert Grade.CONFIRMED == "confirmed"  # str-compatible for psycopg and CHECK text
