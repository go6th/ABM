"""Calibration harness: metric maths (always run) and the eval gate (marker eval, skipped by default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from account_intelligence.grading.config import GradingConfig
from evals.grading_calibration import GoldenItem, evaluate, expected_calibration_error, load_golden, main

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden" / "synthetic.jsonl"


def item(grade: str, conf: float, correct: bool, synthetic: bool = False, key: str = "jobs.a.b") -> GoldenItem:
    return GoldenItem("acct", key, grade, conf, correct, True, synthetic)


def test_metric_maths(cfg: GradingConfig) -> None:
    perfect = [item("A", 1.0, True), item("A", 1.0, True), item("B", 0.0, False)]
    assert expected_calibration_error(perfect) == 0.0
    assert expected_calibration_error([]) is None
    r = evaluate([item("A", 0.9, True), item("B", 0.8, True)], {"acct": ["jobs.a.b"]}, cfg)
    assert (r.precision_a, r.precision_b, r.coverage, r.citation_validity) == (1.0, 1.0, 1.0, 1.0)
    assert r.status == "fail" and not r.gates["calibration_error"]  # 0.85 average confidence vs 100% correct
    assert evaluate([], {}, cfg).status == "blocked"


def test_synthetic_labels_are_always_blocked(cfg: GradingConfig) -> None:
    items, expected = load_golden(GOLDEN)
    report = evaluate(items, expected, cfg)
    assert report.status == "blocked" and report.reason == "needs analyst-reviewed golden accounts"
    assert main([str(GOLDEN)]) == 1


@pytest.mark.eval
def test_eval_gate_on_the_golden_set(cfg: GradingConfig) -> None:
    """Runs only in the eval gate. Fails until analyst-reviewed golden accounts exist."""
    items, expected = load_golden(GOLDEN)
    report = evaluate(items, expected, cfg)
    assert report.status == "pass", f"{report.status}: {report.reason}"
