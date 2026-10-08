"""Skill content as config (work item 1, D-99, D-101)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from account_intelligence.config import CONFIG_DIR
from agents.config import content_hash, load_skill_config, unsourced_values
from agents.errors import AgentConfigInvalid

FILES = [
    "skill/source.yaml",
    "skill/run.yaml",
    "skill/pull_plan.yaml",
    "skill/conversions.yaml",
    "skill/consent.yaml",
    "skill/name_digests.yaml",
    "agents.yaml",
    "lessons.yaml",
    "skill_versions.yaml",
]


def test_every_config_value_has_a_source_or_status() -> None:
    for name in FILES:
        assert unsourced_values(yaml.safe_load((CONFIG_DIR / name).read_text())) == [], name
    assert unsourced_values({"a": {"value": 1}}) == ["a"]
    assert unsourced_values([{"value": 1, "status": "x"}, {"value": 2}]) == ["[1]"]


def test_pull_plan_covers_every_checklist_row() -> None:
    cfg = load_skill_config()
    checklist = yaml.safe_load((CONFIG_DIR / "checklist.yaml").read_text())
    assert set(cfg.plan) == {r["key"] for r in checklist["rows"]} and len(cfg.plan) == 33


def copy_config(tmp_path: Path) -> Path:
    dst = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, dst)
    return dst


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


@pytest.mark.parametrize(
    ("file", "old", "new"),
    [
        (
            "skill/pull_plan.yaml",
            "  research_papers:              {step: pass2,  need: web_search}\n",
            "",
        ),
        (
            "skill/pull_plan.yaml",
            "{step: pass2,  need: web_search}",
            "{step: pass9,  need: web_search}",
        ),
        (
            "skill/pull_plan.yaml",
            "{step: pass2,  need: web_search}",
            "{step: pass2,  need: teleport}",
        ),
        (
            "skill_versions.yaml",
            "in_app_repackaging: {value: false",
            "in_app_repackaging: {value: true",
        ),
        ("skill/run.yaml", "min_phrasings: {value: 3", "min_phrasings: {value: 2"),
        (
            "skill/consent.yaml",
            'scope: {value: people_data, source: "D-97 proposal"}',
            "scope: {value: people_data}",
        ),
    ],
)
def test_invalid_config_is_refused_at_startup(tmp_path, file, old, new) -> None:  # type: ignore[no-untyped-def]
    base = copy_config(tmp_path)
    edit(base / file, old, new)
    with pytest.raises(AgentConfigInvalid):
        load_skill_config(base)


def test_unreadable_config_is_refused(tmp_path: Path) -> None:
    base = copy_config(tmp_path)
    (base / "skill" / "run.yaml").write_text("a: [unclosed")
    with pytest.raises(AgentConfigInvalid):
        load_skill_config(base)


def test_config_version_changes_iff_content_changes(tmp_path: Path) -> None:
    base = copy_config(tmp_path)
    first = load_skill_config(base).config_version
    assert load_skill_config(base).config_version == first
    edit(base / "agents.yaml", "tokens: 4000", "tokens: 4001")
    assert load_skill_config(base).config_version != first
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_skill_facts_from_the_guide() -> None:
    cfg = load_skill_config()
    assert cfg.skill_version == "1.8" and cfg.archive_status == "absent"
    assert cfg.steps == (
        "setup",
        "pass1",
        "pass2",
        "people",
        "distill",
        "build_files",
        "gate",
        "delivery",
    )
    assert cfg.salesforce_includes == ("MuleSoft",)  # D-100, guide S14
    assert cfg.consent_text_version is None  # D-97: no approved text yet
    assert cfg.internal_history_default is False  # D-98
    assert cfg.update_channel == "repository"  # D-101
    assert (cfg.min_phrasings, cfg.min_tool_categories) == (3, 2)


def test_every_prompt_line_is_traced_and_names_no_tool() -> None:
    from agents.naming import client_hits

    for prompt in Path("src/agents").glob("*/prompt.md"):
        lines = [x for x in prompt.read_text().splitlines() if x.strip()]
        assert lines[0].startswith("<!-- agent:"), prompt
        for line in lines[1:]:
            assert re.search(r"\[(S\d+|platform rule \d+)\]$", line), (prompt, line)
            assert not client_hits(line), (prompt, line)
