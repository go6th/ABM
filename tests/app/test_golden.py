"""Golden accounts (roadmap s6): at least three load in tests, and bad baselines are rejected."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.golden import GoldenError, load_golden_accounts
from app.golden.loader import DEFAULT_GOLDEN_DIR, SCHEMA_FILENAME, golden_json_schema
from app.rules import RuleBundle


def test_at_least_three_golden_accounts_load() -> None:
    goldens = load_golden_accounts()
    assert len(goldens) >= 3
    assert [g.golden_id for g in goldens] == sorted(g.golden_id for g in goldens)


def test_shipped_accounts_are_labelled_synthetic() -> None:
    for g in load_golden_accounts():
        assert g.synthetic
        assert g.account.name.startswith("Synthetic ")
        assert g.account.domain.endswith(".example")
        assert "SYNTHETIC" in g.review.source_run


def test_synthetic_accounts_are_refused_as_evidence() -> None:
    with pytest.raises(GoldenError, match="not evidence"):
        load_golden_accounts(allow_synthetic=False)


def test_every_high_row_has_an_expected_status(rules: RuleBundle) -> None:
    for g in load_golden_accounts():
        assert rules.checklist.high_priority_ids <= {c.row_id for c in g.expected_coverage}


def test_schema_file_matches_the_model() -> None:
    on_disk = json.loads((DEFAULT_GOLDEN_DIR / SCHEMA_FILENAME).read_text(encoding="utf-8"))
    assert on_disk == golden_json_schema(), "regenerate golden_account.schema.json"


def _mutate(golden_dir: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = golden_dir / "synthetic_alpha.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _fact(d: dict[str, Any], grade: str) -> dict[str, Any]:
    return next(f for f in d["known_facts"] if f["grade"] == grade)


def _sentence_not_in_source(d: dict[str, Any]) -> None:
    d["known_facts"][0]["supporting_sentence"] = "A sentence nobody wrote."


def _url_not_cited(d: dict[str, Any]) -> None:
    d["known_facts"][0]["url"] = "https://elsewhere.example/page"


def _confirmed_from_secondary(d: dict[str, Any]) -> None:
    lead = _fact(d, "Lead")
    lead["grade"] = "Confirmed"


def _hypothesis_without_question(d: dict[str, Any]) -> None:
    _fact(d, "Hypothesis")["confirming_question"] = None


def _probable_without_reasoning(d: dict[str, Any]) -> None:
    _fact(d, "Probable")["reasoning"] = None


def _unknown_source(d: dict[str, Any]) -> None:
    d["known_facts"][0]["source_ids"] = ["S999"]


def _unknown_row(d: dict[str, Any]) -> None:
    d["expected_coverage"][0]["row_id"] = "P9.99"


def _missing_high_row(d: dict[str, Any]) -> None:
    d["expected_coverage"] = [c for c in d["expected_coverage"] if c["row_id"] != "P2.01"]


def _real_without_reviewer(d: dict[str, Any]) -> None:
    d["synthetic"] = False


def _bad_status(d: dict[str, Any]) -> None:
    d["expected_coverage"][0]["status"] = "Skipped"


def _priority_a_exceeds_total(d: dict[str, Any]) -> None:
    d["expected_people"]["priority_a"] = d["expected_people"]["total"] + 1


def _lanes_do_not_sum(d: dict[str, Any]) -> None:
    d["expected_people"]["by_lane"] = {"Veeva": 1}


def _duplicate_row(d: dict[str, Any]) -> None:
    d["expected_coverage"].append(dict(d["expected_coverage"][0]))


def _duplicate_source(d: dict[str, Any]) -> None:
    d["sources"][1]["source_id"] = d["sources"][0]["source_id"]


def _source_unknown_row(d: dict[str, Any]) -> None:
    d["sources"][0]["row_ids"] = ["P7.01"]


INVALID = [
    (_sentence_not_in_source, "supporting sentence not found"),
    (_url_not_cited, "url is not one of its sources"),
    (_confirmed_from_secondary, "Confirmed needs a primary source"),
    (_hypothesis_without_question, "Hypothesis needs a question"),
    (_probable_without_reasoning, "Probable needs stated reasoning"),
    (_unknown_source, "unknown source"),
    (_unknown_row, "unknown coverage rows"),
    (_missing_high_row, "High rows without an expected status"),
    (_real_without_reviewer, "analyst reviewer"),
    (_bad_status, "status"),
    (_priority_a_exceeds_total, "priority_a"),
    (_lanes_do_not_sum, "sum to total"),
    (_duplicate_row, "appear once"),
    (_duplicate_source, "source ids must be unique"),
    (_source_unknown_row, "unknown coverage rows"),
]


@pytest.mark.parametrize(("change", "message"), INVALID, ids=[c.__name__ for c, _ in INVALID])
def test_invalid_golden_accounts_fail(
    golden_dir: Path, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    _mutate(golden_dir, change)
    with pytest.raises(GoldenError, match=message):
        load_golden_accounts(golden_dir)


def test_duplicate_golden_ids_fail(golden_dir: Path) -> None:
    src = golden_dir / "synthetic_alpha.json"
    (golden_dir / "copy_of_alpha.json").write_text(src.read_text(encoding="utf-8"))
    with pytest.raises(GoldenError, match="unique"):
        load_golden_accounts(golden_dir)


def test_broken_json_fails(golden_dir: Path) -> None:
    (golden_dir / "synthetic_beta.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(GoldenError, match=r"synthetic_beta\.json"):
        load_golden_accounts(golden_dir)


def test_a_reviewed_real_account_is_accepted_as_evidence(golden_dir: Path) -> None:
    for path in golden_dir.glob("synthetic_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["synthetic"] = False
        data["review"] = {
            "reviewed_by": "Analyst owner",
            "reviewed_on": "2026-10-01",
            "source_run": "test only",
        }
        path.write_text(json.dumps(data), encoding="utf-8")
    assert len(load_golden_accounts(golden_dir, allow_synthetic=False)) == 3
