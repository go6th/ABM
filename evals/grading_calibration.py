"""Offline calibration harness for the grading engine (skill reference 06, step 7; ARCH 15).

Reads a golden set of graded claims labelled by analysts and computes the gates in
``config/grading.yaml`` ``eval_gates``: precision of grade A and of grade B, citation validity,
expected calibration error, and expected-attribute coverage. With only synthetic labels the
result is **Blocked: needs analyst-reviewed golden accounts**; no number is a product claim.

    python -m evals.grading_calibration evals/golden/synthetic.jsonl
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from account_intelligence.grading.config import GradingConfig, load_grading_config


@dataclass(frozen=True)
class GoldenItem:
    account: str
    attribute_key: str
    engine_grade: str
    confidence: float
    correct: bool  # analyst verdict on the value
    citation_valid: bool  # analyst verdict on URL + sentence
    synthetic: bool


@dataclass(frozen=True)
class Report:
    status: str  # "pass", "fail", "blocked"
    reason: str | None
    precision_a: float | None
    precision_b: float | None
    citation_validity: float | None
    calibration_error: float | None
    coverage: float | None
    gates: dict[str, bool]


def load_golden(path: Path) -> tuple[list[GoldenItem], dict[str, list[str]]]:
    """JSON lines: claim rows, plus one ``{"expected": {account: [attribute keys]}}`` row."""
    items: list[GoldenItem] = []
    expected: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "expected" in row:
            expected.update(row["expected"])
            continue
        items.append(GoldenItem(**row))
    return items, expected


def _precision(items: Sequence[GoldenItem], grade: str) -> float | None:
    chosen = [i for i in items if i.engine_grade == grade]
    return sum(i.correct for i in chosen) / len(chosen) if chosen else None


def expected_calibration_error(items: Sequence[GoldenItem], bins: int = 10) -> float | None:
    """Weighted mean |accuracy - mean confidence| over equal-width confidence bins."""
    if not items:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [
            i for i in items if lo <= i.confidence < hi or (b == bins - 1 and i.confidence == 1.0)
        ]
        if bucket:
            acc = sum(i.correct for i in bucket) / len(bucket)
            conf = sum(i.confidence for i in bucket) / len(bucket)
            total += len(bucket) / len(items) * abs(acc - conf)
    return total


def evaluate(
    items: Sequence[GoldenItem], expected: dict[str, list[str]], cfg: GradingConfig
) -> Report:
    g = cfg.eval_gates
    pa, pb = _precision(items, "A"), _precision(items, "B")
    cit = sum(i.citation_valid for i in items) / len(items) if items else None
    ece = expected_calibration_error(items)
    want = {(a, k) for a, keys in expected.items() for k in keys}
    have = {(i.account, i.attribute_key) for i in items}
    cov = len(want & have) / len(want) if want else None
    gates = {
        "precision_grade_A": pa is not None and pa >= g.precision_grade_A_min,
        "precision_grade_B": pb is not None and pb >= g.precision_grade_B_min,
        "citation_validity": cit is not None and cit >= g.citation_validity_min,
        "calibration_error": ece is not None and ece <= g.calibration_error_max,
        "coverage_expected_attributes": cov is not None
        and cov >= g.coverage_expected_attributes_min,
    }
    if not items or any(i.synthetic for i in items):
        return Report(
            "blocked", "needs analyst-reviewed golden accounts", pa, pb, cit, ece, cov, gates
        )
    return Report("pass" if all(gates.values()) else "fail", None, pa, pb, cit, ece, cov, gates)


def main(argv: list[str]) -> int:
    items, expected = load_golden(Path(argv[0]))
    report = evaluate(items, expected, load_grading_config())
    print(json.dumps(report.__dict__, indent=1, sort_keys=True))
    return 0 if report.status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
