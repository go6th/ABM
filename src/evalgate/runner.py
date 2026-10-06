"""Runs golden accounts through an executor and computes the metrics (D-50).

The executor is a port: the replay executor (recorded model outputs and provider cassettes, no
network) lives with the test harness; a live executor (real models against cassette providers)
needs credentials and a budget and refuses without them (E9). Per case and per repeat the
executor returns a ``CaseOutput`` read from the stored rows of the run it executed; the runner
never trusts a number from a model.

GUARANTEE(test_runner_aggregates_cases_exactly): known answers over two synthetic accounts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from evalgate import metrics as m
from evalgate.golden import GoldenAccount, GoldenSet


@dataclass(frozen=True)
class CaseOutput:
    """What one executed run produced (from its stored rows)."""

    facts: Mapping[str, tuple[str, str, float | None]]  # attribute -> (value, grade, confidence)
    citations: tuple[m.Citation, ...]
    supported: tuple[bool, ...]
    high_rows: Mapping[str, str]
    costs: tuple[Decimal | None, ...]
    duration_s: float | None
    security_failures: int = 0
    security_checks: int = 0


@dataclass(frozen=True)
class CaseScores:
    """Per-case metric values (for paired tests and flakiness)."""

    account: str
    repeat: int
    values: Mapping[str, float | None]


@dataclass(frozen=True)
class EvalResult:
    """Aggregated metrics with their bases and the per-case scores."""

    metrics: Mapping[str, float | None]
    bases: Mapping[str, tuple[int, int]]
    cases: tuple[CaseScores, ...]
    excluded_stale_labels: int
    extra: Mapping[str, object] = field(default_factory=dict)


class Executor(Protocol):
    """Executes one golden account once."""

    async def run(self, account: GoldenAccount, repeat: int) -> CaseOutput:
        """Run and read back the stored rows."""
        ...


def _norm(v: str) -> str:
    return " ".join(v.casefold().split())


def score_case(account: GoldenAccount, out: CaseOutput, bins: int) -> dict[str, float | None]:
    """Per-case values of each metric."""
    found = {k for k in out.facts}
    pairs = [
        (out.facts[lb.attribute_key][1], lb.grade)
        for lb in account.labels
        if lb.attribute_key in out.facts
    ]
    acc = m.grade_accuracy(pairs)
    calib = [
        (c, _norm(v) == _norm(lb.value))
        for lb in account.labels
        if lb.attribute_key in out.facts
        for v, _, c in [out.facts[lb.attribute_key]]
        if c is not None
    ]
    cost = m.run_cost(out.costs)
    return {
        "coverage": m.coverage(account.expected_attributes, found).value,
        "high_row_coverage": m.high_row_coverage(out.high_rows).value,
        "citation_validity": m.citation_validity(out.citations).value,
        "precision_grade_a": acc.precision["confirmed"].value,
        "precision_grade_b": acc.precision["probable"].value,
        "unsupported_claim_rate": m.unsupported_rate(out.supported).value,
        "calibration_error": m.calibration_error(calib, bins),
        "cost_per_run": None if cost is None else float(cost),
        "duration_s": out.duration_s,
    }


async def run(golden: GoldenSet, executor: Executor, repeats: int, bins: int) -> EvalResult:
    """Every account ``repeats`` times (synthetic accounts count only toward self-tests);
    aggregate over all cases from the pooled bases (never an average of averages)."""
    outputs: list[tuple[GoldenAccount, int, CaseOutput]] = []
    # LOOP(eval repeats): cap=accounts x repeats_k runs; exit=all cases executed
    for account in golden.accounts:
        for r in range(repeats):
            outputs.append((account, r, await executor.run(account, r)))
    return aggregate(outputs, bins, golden.stale_excluded)


def aggregate(
    outputs: Sequence[tuple[GoldenAccount, int, CaseOutput]], bins: int, stale: int = 0
) -> EvalResult:
    """Pooled metrics with bases; per-case scores alongside."""
    cov_n = cov_d = hr_n = hr_d = cit_n = cit_d = uns_n = uns_d = 0
    pairs: list[tuple[str, str]] = []
    calib: list[tuple[float, bool]] = []
    costs: list[float] = []
    durations: list[float] = []
    sec_fail = sec_total = 0
    cases: list[CaseScores] = []
    for account, r, out in outputs:
        c = m.coverage(account.expected_attributes, out.facts)
        cov_n, cov_d = cov_n + c.numerator, cov_d + c.denominator
        h = m.high_row_coverage(out.high_rows)
        hr_n, hr_d = hr_n + h.numerator, hr_d + h.denominator
        ct = m.citation_validity(out.citations)
        cit_n, cit_d = cit_n + ct.numerator, cit_d + ct.denominator
        u = m.unsupported_rate(out.supported)
        uns_n, uns_d = uns_n + u.numerator, uns_d + u.denominator
        for lb in account.labels:
            if lb.attribute_key in out.facts:
                value, grade, conf = out.facts[lb.attribute_key]
                pairs.append((grade, lb.grade))
                if conf is not None:
                    calib.append((conf, _norm(value) == _norm(lb.value)))
        cost = m.run_cost(out.costs)
        if cost is not None:
            costs.append(float(cost))
        if out.duration_s is not None:
            durations.append(out.duration_s)
        sec_fail += out.security_failures
        sec_total += out.security_checks
        cases.append(CaseScores(account.account, r, score_case(account, out, bins)))
    acc = m.grade_accuracy(pairs)
    total_costs = len(costs) == len(outputs) and bool(outputs)
    metrics_out: dict[str, float | None] = {
        "coverage": m.Ratio(cov_n, cov_d).value,
        "high_row_coverage": m.Ratio(hr_n, hr_d).value,
        "citation_validity": m.Ratio(cit_n, cit_d).value,
        "precision_grade_a": acc.precision["confirmed"].value,
        "precision_grade_b": acc.precision["probable"].value,
        "unsupported_claim_rate": m.Ratio(uns_n, uns_d).value,
        "calibration_error": m.calibration_error(calib, bins),
        "cost_per_run": sum(costs) / len(costs) if total_costs else None,
        "p95_duration_s": m.p95(durations),
        "security_pass_rate": None if sec_total == 0 else (sec_total - sec_fail) / sec_total,
    }
    bases = {
        "coverage": (cov_n, cov_d),
        "high_row_coverage": (hr_n, hr_d),
        "citation_validity": (cit_n, cit_d),
        "unsupported_claim_rate": (uns_n, uns_d),
        "precision_grade_a": (
            acc.precision["confirmed"].numerator,
            acc.precision["confirmed"].denominator,
        ),
        "precision_grade_b": (
            acc.precision["probable"].numerator,
            acc.precision["probable"].denominator,
        ),
        "security_pass_rate": (sec_total - sec_fail, sec_total),
    }
    return EvalResult(metrics_out, bases, tuple(cases), stale)


def paired_scores(
    baseline: EvalResult, candidate: EvalResult, metric: str
) -> tuple[list[float], list[float]]:
    """Per-case values of one metric present in both runs, matched by (account, repeat)."""
    base = {(c.account, c.repeat): c.values.get(metric) for c in baseline.cases}
    out_b: list[float] = []
    out_c: list[float] = []
    for c in sorted(candidate.cases, key=lambda x: (x.account, x.repeat)):
        b = base.get((c.account, c.repeat))
        v = c.values.get(metric)
        if b is not None and v is not None:
            out_b.append(b)
            out_c.append(v)
    return out_b, out_c
