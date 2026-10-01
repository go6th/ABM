"""Checklist to config (roadmap s6): every rule file loads, validates and is versioned."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.rules import ConfigError, RuleBundle, load_rules
from app.rules.schemas import CoverageStatus, GradeName, Priority
from tests.app.conftest import EditYaml


def test_all_33_coverage_rows_load(rules: RuleBundle) -> None:
    rows = rules.checklist.rows
    assert len(rows) == 33
    assert sum(r.pass_ == 1 for r in rows) == 5
    assert sum(r.pass_ == 2 for r in rows) == 28
    assert len({r.id for r in rows}) == 33


def test_priorities_match_prd_appendix_a(rules: RuleBundle) -> None:
    counts = {p: sum(r.priority is p for r in rules.checklist.rows) for p in Priority}
    assert counts == {Priority.HIGH: 19, Priority.MEDIUM: 7, Priority.OPTIONAL: 7}
    assert rules.checklist.row("P2.10").priority is Priority.MEDIUM
    assert rules.checklist.row("P2.28").row == "Research papers"


def test_every_row_has_a_shelf_life_entry(rules: RuleBundle) -> None:
    cl = rules.checklist
    assert cl.row("P2.04").shelf_life == "job_board"
    assert cl.shelf_lives["job_board"].days == 7
    assert cl.shelf_lives["earnings"].days == 90
    # Rows with no cadence in the sources are reported, not given invented numbers.
    unset = cl.rows_without_shelf_life()
    assert "P1.01" in unset
    assert "P2.01" not in unset


def test_unknown_row_lookup_raises(rules: RuleBundle) -> None:
    with pytest.raises(KeyError):
        rules.checklist.row("P3.01")


def test_statuses_are_the_five_from_the_prd(rules: RuleBundle) -> None:
    assert set(rules.checklist.statuses) == set(CoverageStatus)
    assert {s.value for s in CoverageStatus} == {"Done", "Partial", "Not done", "Blocked", "None"}


def test_grading_trust_model(rules: RuleBundle) -> None:
    g = rules.grading
    assert [x.name for x in g.grades if x.may_state_to_prospect] == [GradeName.CONFIRMED]
    assert g.grade("Hypothesis").requires_confirming_question
    assert g.grade("Probable").requires_reasoning
    assert g.rules.absence_of_evidence_grade is GradeName.HYPOTHESIS
    assert g.rules.lead_verification.min_distinct_queries == 3
    assert g.rules.lead_verification.min_distinct_tools == 2
    with pytest.raises(KeyError):
        g.grade("Certain")


def test_routing_table(rules: RuleBundle) -> None:
    r = rules.routing
    assert r.route("people_by_title").first_choice.broker == "bitscale"
    assert r.route("people_by_title").fallback is not None
    assert r.route("profile_enrichment").fallback is None
    assert "json_extraction" in r.route("scrape").avoid_operations
    with pytest.raises(KeyError):
        r.route("telepathy")


def test_client_banned_terms_cover_every_paid_broker(rules: RuleBundle) -> None:
    banned = rules.routing.client_banned_terms()
    for key, broker in rules.routing.brokers.items():
        if key != "builtin":
            assert broker.display_name.lower() in banned
    assert banned == sorted(banned)


def test_output_spec_keeps_operator_file_out_of_the_zip(rules: RuleBundle) -> None:
    spec = rules.output_spec
    by_id = {d.id: d for d in spec.deliverables}
    assert by_id["operator_only"].audience.value == "operator_only"
    assert "operator_only" not in by_id["workspace"].includes
    assert {"summary", "evidence", "people", "jobs", "tech", "workspace"} <= set(by_id)
    assert "\u2014" in spec.document_rules.banned_characters


def test_every_model_task_is_routed_in_every_arm(rules: RuleBundle) -> None:
    m = rules.models
    for arm in m.arms.values():
        assert set(arm.routes) == set(m.tasks)
    assert m.decision_status == "Proposed"


def test_version_is_recorded(rules: RuleBundle) -> None:
    assert set(rules.file_versions) == {
        "checklist.yaml",
        "grading.yaml",
        "routing.yaml",
        "output_spec.yaml",
        "models.yaml",
        "people_scope.yaml",
    }
    assert re.fullmatch(r"([a-z]+\d+\.\d+\.\d+-?)+\+[0-9a-f]{12}", rules.version)
    assert load_rules().version == rules.version  # deterministic


def test_version_changes_when_content_changes(config_dir: Path, edit_yaml: EditYaml) -> None:
    before = load_rules(config_dir).version

    def rename(d: dict[str, Any]) -> None:
        d["rows"][0]["row"] = "Prior customer status and history"

    edit_yaml("checklist.yaml", rename)
    after = load_rules(config_dir)
    assert after.version != before
    # Declared version unchanged, so only the hash moved: the edit is still detectable.
    assert after.file_versions["checklist.yaml"] == "1.0.0"


def _drop_row(d: dict[str, Any]) -> None:
    d["rows"].pop()


def _dup_row(d: dict[str, Any]) -> None:
    d["rows"][1]["id"] = d["rows"][0]["id"]


def _bad_priority(d: dict[str, Any]) -> None:
    d["rows"][0]["priority"] = "Urgent"


def _wrong_pass(d: dict[str, Any]) -> None:
    d["rows"][0]["pass"] = 2


def _unknown_shelf(d: dict[str, Any]) -> None:
    d["rows"][0]["shelf_life"] = "fortnightly"


def _extra_field(d: dict[str, Any]) -> None:
    d["rows"][0]["owner"] = "someone"


def _bad_semver(d: dict[str, Any]) -> None:
    d["version"] = "v1"


def _lead_may_state(d: dict[str, Any]) -> None:
    d["grades"][3]["may_state_to_prospect"] = True


def _drop_grade(d: dict[str, Any]) -> None:
    d["grades"].pop()


def _weak_lead_rule(d: dict[str, Any]) -> None:
    d["rules"]["lead_verification"]["min_distinct_queries"] = 2


def _absence_confirms(d: dict[str, Any]) -> None:
    d["rules"]["absence_of_evidence_grade"] = "Probable"


def _no_url_required(d: dict[str, Any]) -> None:
    d["required_fact_fields"] = ["grade", "supporting_sentence"]


def _aggregator_confirmed(d: dict[str, Any]) -> None:
    d["rules"]["aggregator_allowed_grades"] = ["Lead", "Confirmed"]


def _unknown_broker(d: dict[str, Any]) -> None:
    d["routes"][0]["fallback"]["broker"] = "mystery"


def _self_fallback(d: dict[str, Any]) -> None:
    d["routes"][0]["fallback"] = dict(d["routes"][0]["first_choice"])


def _pull_twice(d: dict[str, Any]) -> None:
    d["connector_policy"]["pull_once_per_run"] = False


def _duplicate_need(d: dict[str, Any]) -> None:
    d["routes"].append(dict(d["routes"][0]))


def _zip_operator_file(d: dict[str, Any]) -> None:
    for item in d["deliverables"]:
        if item["id"] == "workspace":
            item["includes"].append("operator_only")


def _no_placeholder(d: dict[str, Any]) -> None:
    d["deliverables"][0]["filename"] = "Account_Summary"


def _unknown_include(d: dict[str, Any]) -> None:
    d["deliverables"][5]["includes"].append("deck")


def _unrouted_task(d: dict[str, Any]) -> None:
    del d["arms"]["split"]["routes"]["narrative"]


def _unknown_profile(d: dict[str, Any]) -> None:
    d["arms"]["split"]["routes"]["narrative"] = "huge"


def _unknown_active_arm(d: dict[str, Any]) -> None:
    d["active_arm"] = "triple"


def _name_first(d: dict[str, Any]) -> None:
    d["merge_keys"] = ["name", "profile_url"]


INVALID: list[tuple[str, Callable[[dict[str, Any]], None], str]] = [
    ("checklist.yaml", _drop_row, "must have 28 rows"),
    ("checklist.yaml", _dup_row, "duplicate coverage row"),
    ("checklist.yaml", _bad_priority, "priority"),
    ("checklist.yaml", _wrong_pass, "declares pass"),
    ("checklist.yaml", _unknown_shelf, "undefined shelf lives"),
    ("checklist.yaml", _extra_field, "Extra inputs"),
    ("checklist.yaml", _bad_semver, "semantic"),
    ("grading.yaml", _lead_may_state, "only Confirmed"),
    ("grading.yaml", _drop_grade, "exactly Confirmed"),
    ("grading.yaml", _weak_lead_rule, "3 queries across 2 tools"),
    ("grading.yaml", _absence_confirms, "Hypothesis"),
    ("grading.yaml", _no_url_required, "grade, url and supporting_sentence"),
    ("grading.yaml", _aggregator_confirmed, "aggregator"),
    ("routing.yaml", _unknown_broker, "unknown broker"),
    ("routing.yaml", _self_fallback, "falls back to itself"),
    ("routing.yaml", _pull_twice, "pull_once_per_run"),
    ("routing.yaml", _duplicate_need, "routed once"),
    ("output_spec.yaml", _zip_operator_file, "cannot include"),
    ("output_spec.yaml", _no_placeholder, "{account}"),
    ("output_spec.yaml", _unknown_include, "unknown deliverable"),
    ("models.yaml", _unrouted_task, "missing tasks"),
    ("models.yaml", _unknown_profile, "unknown profiles"),
    ("models.yaml", _unknown_active_arm, "active_arm"),
    ("people_scope.yaml", _name_first, "profile_url first"),
]


@pytest.mark.parametrize(
    ("filename", "change", "message"), INVALID, ids=[f"{f}:{c.__name__}" for f, c, _ in INVALID]
)
def test_invalid_configs_fail(
    edit_yaml: EditYaml,
    config_dir: Path,
    filename: str,
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    edit_yaml(filename, change)
    with pytest.raises(ConfigError, match=re.escape(message)):
        load_rules(config_dir)


def test_missing_file_fails(config_dir: Path) -> None:
    (config_dir / "grading.yaml").unlink()
    with pytest.raises(ConfigError, match="missing config file"):
        load_rules(config_dir)


def test_unparsable_yaml_fails(config_dir: Path) -> None:
    (config_dir / "routing.yaml").write_text("routes: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_rules(config_dir)


def test_non_mapping_yaml_fails(config_dir: Path) -> None:
    (config_dir / "models.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_rules(config_dir)
