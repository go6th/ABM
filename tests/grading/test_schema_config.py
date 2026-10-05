"""Schema validators hold whoever constructs a GradedClaim (G2, G5, G6, G8, G9, G13), and
configuration fails loudly (typed ConfigError) on any malformed or invented value."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.grading.config import GradingConfig, load_grading_config
from account_intelligence.grading.errors import ConfigError
from account_intelligence.grading.schema import (
    PROPOSED_ADAPTER,
    GradedClaim,
    GradedEvidence,
    LeadRecord,
)

WHEN = datetime(2026, 10, 1, tzinfo=UTC)
SRC = dict(
    evidence_kind="source",
    url="https://acme.example/a",
    supporting_sentence="Jane Doe is the CTO of Acme.",
    span_sha256="0" * 64,
    source_ref="r#0",
    call_ref="c1",
    origin="full_page_read",
    tier="T1",
    lineage_group="account_primary",
    is_aggregator=False,
    date_kind="observed",
    evidence_date=WHEN,
    provider_code="p_scrape",
)


def claim(**over: Any) -> GradedClaim:
    base: dict[str, Any] = dict(
        schema_version=1,
        config_hash="h",
        claim_hash="c",
        claim_kind="sourced",
        attribute_key="exec.cto.name",
        value="Jane Doe",
        normalised_value="jane doe",
        product_grade="confirmed",
        engine_grade="B",
        confidence=0.9,
        read_in_primary_this_run=True,
        may_state_to_prospect=True,
        precedence_rule="primary_read",
        evidence=(GradedEvidence(**SRC),),
        confirming_question=None,
        reasoning=None,
        flags=(),
        lead=None,
    )
    base.update(over)
    return GradedClaim(**base)


def test_a_valid_confirmed_claim_builds() -> None:
    assert claim().product_grade == "confirmed"


@pytest.mark.parametrize(
    ("over", "match"),
    [
        (dict(read_in_primary_this_run=False), "primary read"),
        (dict(may_state_to_prospect=False), "stated to a prospect"),
        (dict(product_grade="probable", may_state_to_prospect=True), "stated to a prospect"),
        (dict(product_grade="hypothesis", may_state_to_prospect=False), "confirming question"),
        (dict(product_grade="probable", may_state_to_prospect=False), "reasoning"),
        (dict(product_grade="lead", may_state_to_prospect=False), "verification record"),
        (dict(flags=("conflicting",)), "confirming question"),
        (dict(claim_kind="synthesis"), "inherited"),
        (dict(evidence=()), "at least 1"),
        (
            dict(
                claim_kind="absence",
                product_grade="probable",
                may_state_to_prospect=False,
                reasoning="r",
                evidence=(GradedEvidence(**{**SRC, "evidence_kind": "search_proof", "url": None}),),
            ),
            "hypotheses",
        ),
        (dict(product_grade="confirmed", grade="A"), "Extra inputs"),
    ],
)
def test_graded_claim_rules_hold_for_any_constructor(over: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        claim(**over)


def test_evidence_must_be_traceable() -> None:
    with pytest.raises(ValidationError, match="needs"):
        GradedEvidence(**{**SRC, "url": None})
    with pytest.raises(ValidationError, match="call it proves"):
        GradedEvidence(**{**SRC, "evidence_kind": "search_proof", "call_ref": None})
    lead = claim(
        product_grade="lead",
        may_state_to_prospect=False,
        lead=LeadRecord(status="pending", reason=None, accepted_queries=0, tool_categories=()),
    )
    assert lead.lead and lead.lead.status == "pending"
    assert claim(flags=("b", "a", "a")).flags == ("a", "b")


def test_proposed_claim_has_no_grade_fields() -> None:
    schema = PROPOSED_ADAPTER.json_schema()
    text = str(schema)
    for forbidden in (
        "product_grade",
        "engine_grade",
        "confidence",
        "tier",
        "lineage",
        "read_in_primary",
    ):
        assert forbidden not in text


# ------------------------------------------------------------------ config


def _copy(tmp_path: Path) -> Path:
    for name in ("grading.yaml", "grading_policy.yaml", "source_registry.yaml", "routing.yaml"):
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    return tmp_path


def _edit(path: Path, fn: Any) -> None:
    data = yaml.safe_load(path.read_text())
    fn(data)
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize(
    ("file", "mutate"),
    [
        ("grading.yaml", lambda d: d["engine"]["grades"]["A"].update(rule="c is high")),
        ("grading.yaml", lambda d: d["engine"]["grades"]["B"].update(rule="c >= high")),
        ("grading.yaml", lambda d: d["engine"].update(conflicting="when they disagree")),
        ("grading.yaml", lambda d: d["engine"]["source_tiers"].pop("T4_scraped_aggregated")),
        ("grading.yaml", lambda d: d["engine"]["source_tiers"]["T1_primary"].update(weight=1.5)),
        ("grading.yaml", lambda d: d["engine"]["half_life_days"].update(hiring=0)),
        ("grading_policy.yaml", lambda d: d["attributes"]["classes"].update(exec="weather")),
        (
            "grading_policy.yaml",
            lambda d: d["tool_category_by_operation"].update(web_search="telepathy"),
        ),
        ("grading_policy.yaml", lambda d: d["origin_by_operation"].pop("status")),
        ("grading_policy.yaml", lambda d: d["judge"].update(max_reasks=9)),
        (
            "source_registry.yaml",
            lambda d: d["provider_codes"].update({"SomeVendor": d["provider_codes"]["p_org"]}),
        ),
        ("routing.yaml", lambda d: d.pop("tool_categories")),
    ],
)
def test_malformed_config_raises_config_error(tmp_path: Path, file: str, mutate: Any) -> None:
    root = _copy(tmp_path)
    _edit(root / file, mutate)
    with pytest.raises(ConfigError):
        load_grading_config(root)


def test_unreadable_config_and_stable_hash(tmp_path: Path, cfg: GradingConfig) -> None:
    root = _copy(tmp_path)
    assert load_grading_config(root).content_hash == cfg.content_hash
    (root / "grading.yaml").write_text("engine: [unclosed")
    with pytest.raises(ConfigError):
        load_grading_config(root)
    (root / "grading.yaml").unlink()
    with pytest.raises(ConfigError):
        load_grading_config(root)


def test_routing_tool_categories_equal_the_skill_asset() -> None:
    skill = (
        Path(__file__).resolve().parents[2]
        / ".claude/skills/account-intelligence-platform/assets/routing.yaml"
    )
    assert yaml.safe_load(skill.read_text())["tool_categories"] == list(
        load_grading_config().tool_categories
    )


def test_every_proposal_block_is_labelled() -> None:
    policy = yaml.safe_load((CONFIG_DIR / "grading_policy.yaml").read_text())
    assert policy["status"] == "proposal"
    for name, block in policy.items():
        if isinstance(block, dict):
            assert block.get("status") == "proposal", name
    reg = yaml.safe_load((CONFIG_DIR / "source_registry.yaml").read_text())
    assert reg["status"] == "proposal" and all(
        "TODO" in v["source"] for v in reg["provider_codes"].values()
    )
