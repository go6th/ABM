"""Phase 0 benchmark harnesses: scoring logic, offline runs, output files."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.benchmarks import model_split, people_source
from app.benchmarks.common import Provenance, to_markdown
from app.decision_log import EvidenceRefused
from app.golden import load_golden_accounts
from app.llm import LiveProviderUnavailable


@pytest.fixture(scope="module")
def alpha():  # type: ignore[no-untyped-def]
    return next(g for g in load_golden_accounts() if g.golden_id == "synthetic_alpha")


def _perfect_facts(golden) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    facts = []
    for s in golden.sources:
        for row in s.row_ids:
            for k in golden.known_facts:
                if k.supporting_sentence in s.text:
                    facts.append(
                        {
                            "row_id": row,
                            "url": str(s.url),
                            "supporting_sentence": k.supporting_sentence,
                        }
                    )
    return facts


def test_score_perfect_answer(alpha) -> None:  # type: ignore[no-untyped-def]
    s = model_split.score(alpha, _perfect_facts(alpha))
    assert s.citation_validity == 1.0
    assert s.fact_recall == 1.0


def test_score_catches_miscitation_and_invented_sentences(alpha) -> None:  # type: ignore[no-untyped-def]
    good = _perfect_facts(alpha)[0]
    wrong_url = {**good, "url": "https://alpha-synthetic.example/careers/synthetic-posting-42"}
    invented = {**good, "supporting_sentence": "The account plans to triple revenue."}
    s = model_split.score(alpha, [good, wrong_url, invented])
    assert s.citation_validity == pytest.approx(1 / 3)
    assert s.facts_returned == 3


def test_score_handles_failed_answers(alpha) -> None:  # type: ignore[no-untyped-def]
    assert model_split.parse_facts("not json") is None
    assert model_split.parse_facts('{"facts": "nope"}') is None
    assert model_split.parse_facts("[1, 2]") is None
    assert model_split.parse_facts('{"facts": [1, {"a": 1}]}') == [{"a": 1}]
    s = model_split.score(alpha, None)
    assert s.citation_validity is None
    assert s.coverage == 0.0
    assert s.fact_recall == 0.0


def test_coverage_counts_only_rows_with_evidence_expected(alpha) -> None:  # type: ignore[no-untyped-def]
    facts = [f for f in _perfect_facts(alpha) if f["row_id"] == "P2.01"]
    s = model_split.score(alpha, facts)
    assert 0 < s.coverage < 1


def test_prompt_carries_the_source_pack(alpha) -> None:  # type: ignore[no-untyped-def]
    prompt = model_split.build_prompt(alpha)
    _, _, payload = prompt.partition(model_split.SOURCE_PACK_MARKER)
    assert {p["source_id"] for p in json.loads(payload)} == {s.source_id for s in alpha.sources}
    assert "untrusted data" in model_split.SYSTEM_PROMPT


def test_offline_run_compares_both_arms() -> None:
    rows = model_split.run(["single", "split"])
    summary = {r["arm"]: r for r in rows if str(r["account"]).startswith("ALL")}
    assert set(summary) == {"single", "split"}
    assert summary["single"]["citation_validity"] == 1.0
    assert summary["single"]["fact_recall"] == 1.0
    split_validity = summary["split"]["citation_validity"]
    assert isinstance(split_validity, float) and split_validity < 1.0
    assert summary["single"]["cost_usd"] is None  # prices not configured


def test_model_split_cli_writes_csv_and_markdown(tmp_path: Path) -> None:
    assert model_split.main(["--out", str(tmp_path)]) == 0
    with (tmp_path / "model_split.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3 * 2 + 2
    md = (tmp_path / "model_split.md").read_text()
    assert "NOT EVIDENCE" in md
    assert "Config version" in md


def test_model_split_live_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LiveProviderUnavailable, match="ANTHROPIC_API_KEY"):
        model_split.main(["--live", "--out", str(tmp_path)])


def test_benchmarks_refuse_to_log_fake_evidence(tmp_path: Path) -> None:
    log = tmp_path / "log.md"
    log.write_text(
        "<!-- BEGIN EVIDENCE: model_split -->\nx\n<!-- END EVIDENCE: model_split -->\n"
        "<!-- BEGIN EVIDENCE: people_source -->\nx\n<!-- END EVIDENCE: people_source -->\n"
    )
    with pytest.raises(EvidenceRefused):
        model_split.main(["--out", str(tmp_path), "--decision-log", str(log)])
    with pytest.raises(EvidenceRefused):
        people_source.main(["--out", str(tmp_path), "--decision-log", str(log)])
    model_split.main(["--out", str(tmp_path), "--decision-log", str(log), "--allow-non-evidence"])
    assert "NOT EVIDENCE" in log.read_text()


def test_people_source_comparison() -> None:
    rows = {r["account"]: r for r in people_source.run()}
    alpha = rows["synthetic_alpha"]
    assert alpha["found_bitscale_only_arm"] == 5
    assert alpha["found_combined_arm"] == 7 == alpha["expected_people"]
    assert alpha["overlap"] == 2
    assert alpha["added_by_sumble"] == 2
    assert alpha["credits_bitscale_only_arm"] == 0
    assert alpha["credits_combined_arm"] == 10
    assert alpha["credits_per_added_person"] == 5
    assert all(r["duplicate_paid_pulls"] == 0 for r in rows.values())
    total = rows["ALL (total)"]
    assert total["found_combined_arm"] == 22
    assert total["credits_per_added_person"] == pytest.approx(32 / 7)


def test_people_source_cli_writes_files(tmp_path: Path) -> None:
    assert people_source.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "people_source.csv").exists()
    assert "NOT EVIDENCE" in (tmp_path / "people_source.md").read_text()


def test_people_payload_must_have_a_people_list() -> None:
    with pytest.raises(ValueError, match="no people list"):
        people_source._records({"rows": []}, "bitscale")


def test_provenance_rules() -> None:
    assert Provenance("live", "real").is_evidence
    assert not Provenance("live", "synthetic").is_evidence
    assert not Provenance("fake", "real").is_evidence
    assert not Provenance("recorded", "real").is_evidence
    assert "NOT EVIDENCE" in Provenance("recorded", "real").label()


def test_markdown_table() -> None:
    md = to_markdown([{"a": 1, "b": None, "c": 0.5}], ["a", "b", "c"])
    assert md.splitlines()[-1] == "| 1 | n/a | 0.500 |"
