"""Config and rubric: every rule sourced, every example holds (spec 2.2, 9.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from people.config import PeopleConfig, load_people_config
from people.errors import ConfigError
from people.exclusion import check_exclusion
from people.lane import lanes_for
from people.level import classify_level
from people.priority import assign_priority

CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_config_loads_and_hash_is_stable(cfg: PeopleConfig) -> None:
    assert len(cfg.config_hash) == 64
    load_people_config.cache_clear()
    assert load_people_config().config_hash == cfg.config_hash
    assert cfg.cap("max_gap_passes") == 2
    assert cfg.levels_in_scope == {
        "c_level",
        "evp",
        "svp",
        "vp",
        "head",
        "director",
        "senior_manager",
    }


def test_every_level_rule_example_holds(cfg: PeopleConfig) -> None:
    for rule in cfg.level_rules:
        hit = classify_level(rule.positive, cfg.level_rules, cfg.level_order)
        assert hit is not None and hit.level == rule.value, rule.rule_id
        miss = classify_level(rule.negative, cfg.level_rules, cfg.level_order)
        assert miss is None or miss.level != rule.value, rule.rule_id


def test_every_exclusion_and_review_example_holds(cfg: PeopleConfig) -> None:
    for rule in cfg.exclusion_rules:
        assert check_exclusion(rule.positive, cfg.exclusion_rules, ()).rule_id == rule.rule_id
        assert check_exclusion(rule.negative, cfg.exclusion_rules, ()).outcome == "include"
    for rule in cfg.review_rules:
        verdict = check_exclusion(rule.positive, cfg.exclusion_rules, cfg.review_rules)
        assert verdict.outcome == "review" and verdict.rule_id == rule.rule_id
        assert check_exclusion(rule.negative, (), cfg.review_rules).outcome == "include"


def test_every_priority_example_holds(cfg: PeopleConfig) -> None:
    for rule in cfg.priority_rules:
        for example, expect in ((rule.positive, True), (rule.negative, False)):
            level = classify_level(example, cfg.level_rules, cfg.level_order)
            assert level is not None
            band = cfg.band_of_level[level.level]
            lanes = lanes_for(example, None, cfg.lanes)
            got = assign_priority(band, bool(lanes), cfg.priority_rules)
            assert (got is not None and got.rule_id == rule.rule_id) is expect, rule.rule_id


def test_every_rubric_entry_has_source_or_status() -> None:
    rubric = yaml.safe_load((CONFIG / "people_rubric.yaml").read_text())
    assert rubric["status"] == "proposal"
    for section in ("level_rules", "exclusion_rules", "review_rules", "priority_rules"):
        for entry in rubric[section]:
            assert "source" in entry or "status" in entry, entry
    for name, cap in rubric["caps"].items():
        assert "source" in cap or "status" in cap, name
    scope = yaml.safe_load((CONFIG / "people_scope.yaml").read_text())
    for key in ("levels_in_scope", "excluded_title_terms", "merge_keys"):
        assert f"{key}_source" in scope


def _broken(tmp_path: Path, edit: str, find: str, replace: str) -> Path:
    for name in (
        "people_scope.yaml",
        "people_rubric.yaml",
        "job_lanes.yaml",
        "posting_rubric.yaml",
        "jobs_v1.yaml",
    ):
        text = (CONFIG / name).read_text()
        if name == edit:
            assert find in text, find
            text = text.replace(find, replace, 1)
        (tmp_path / name).write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    ("edit", "find", "replace"),
    [
        (
            "people_rubric.yaml",
            'status: proposal,\n     source: "PRD s3.3 names Director"',
            'source_x: "none"',
        ),
        ("people_rubric.yaml", 'negative: "Directory Services Analyst"', 'negative: ""'),
        ("people_scope.yaml", "  - it audit\n", ""),
        ("people_rubric.yaml", "  manager: [senior_manager]", "  manager: []"),
        ("people_rubric.yaml", "    - {pass: 2, band: manager}\n", ""),
        ("people_rubric.yaml", "value: platform_and_level", "value: guess"),
        ("people_rubric.yaml", "max_months: 12}", "max_months: 11}"),
        ("people_rubric.yaml", "match: lane_lexicon", "match: titles"),
        ("people_rubric.yaml", "priority: A, band: senior", "priority: D, band: senior"),
        ("people_rubric.yaml", "level_order: [c_level,", "level_order: ["),
        ("people_rubric.yaml", "cues: [chief]", "cues: []"),
        ("people_scope.yaml", "geography:", "geo:"),
        (
            "people_rubric.yaml",
            "    - {id: under_1y, min_months: 0,",
            "    - {id: under_1y, min_months: 1,",
        ),
        ("people_rubric.yaml", "max_months: null}", "max_months: 99}"),
        (
            "people_rubric.yaml",
            "  max_people_per_run: {value: 400, status: proposal}",
            "  max_people_per_run: {value: 400}",
        ),
        ("people_rubric.yaml", "version:", "- broken\nversion:"),
        ("job_lanes.yaml", "  - id: sap\n    label: SAP\n", "  - id: sap\n"),
    ],
)
def test_invalid_config_is_refused(tmp_path: Path, edit: str, find: str, replace: str) -> None:
    load_people_config.cache_clear()
    with pytest.raises(ConfigError):
        load_people_config(_broken(tmp_path, edit, find, replace))
    load_people_config.cache_clear()


def test_missing_file_and_unknown_cap(tmp_path: Path, cfg: PeopleConfig) -> None:
    with pytest.raises(ConfigError):
        load_people_config(tmp_path)
    with pytest.raises(ConfigError):
        cfg.cap("no_such_cap")
