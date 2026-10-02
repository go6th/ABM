"""One key space for coverage rows (spec-compliance finding SC-1).

The skill's ``assets/checklist.yaml`` is canonical. ``config/checklist.yaml`` carries the
service-design shelf lives for the same rows and must agree with it key for key; the workflow
stage map must own exactly the canonical keys. GUARANTEE(test_every_row_has_exactly_one_owner)
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from account_intelligence.workflow.spec import load_spec

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude/skills/account-intelligence-platform/assets/checklist.yaml"
CONFIG = ROOT / "config/checklist.yaml"


def _skill_rows() -> list[dict[str, object]]:
    return list(yaml.safe_load(SKILL.read_text())["rows"])


def test_checklist_counts_come_from_the_file() -> None:
    rows = _skill_rows()
    assert len(rows) == 33
    assert Counter(r["pass"] for r in rows) == {1: 5, 2: 28}
    assert Counter(r["priority"] for r in rows) == {"high": 19, "medium": 7, "optional": 7}


def test_config_checklist_matches_canonical_keys_labels_priorities() -> None:
    canonical = _skill_rows()
    config = yaml.safe_load(CONFIG.read_text())["rows"]
    assert [r["key"] for r in config] == [r["key"] for r in canonical]
    assert [r["row"] for r in config] == [r["label"] for r in canonical]
    assert [str(r["priority"]).lower() for r in config] == [r["priority"] for r in canonical]
    assert [r["pass"] for r in config] == [r["pass"] for r in canonical]


def test_every_row_has_exactly_one_owner() -> None:
    spec = load_spec()
    assert set(spec.coverage_owners) == {str(r["key"]) for r in _skill_rows()}
    owned = [row for stage in spec.stages for row in stage.coverage]
    assert len(owned) == len(set(owned)) == 33


def test_stage_map_doc_is_generated_and_current() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gen_stage_map.py"), "--check"], check=False
    )
    assert result.returncode == 0, "docs/workflow/stage-map.md is stale"
