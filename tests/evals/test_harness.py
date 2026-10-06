"""EvalGuard E1 to E10 on pure code: metric and statistics known answers, the gate, the A/A
false-block bound, seeded regressions that must block, golden integrity and contamination,
security evals, immutable baselines and deterministic reports."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from evalgate import (
    adversarial,
    bench,
    calibrate,
    contamination,
    gates,
    golden,
    metrics,
    report,
    stats,
)
from evalgate.baseline import BaselineExists, Provenance, from_json, to_json, write_new
from evalgate.cli import main as cli
from evalgate.config import EvalConfigInvalid, build, load_eval_config
from evalgate.runner import CaseOutput, aggregate, paired_scores

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evals/golden/labels"
CFG = load_eval_config()
PROV = Provenance("a" * 40, "b" * 64, "c" * 64, "d" * 64, "replay")


def test_eval_config_is_sourced() -> None:
    raw = yaml.safe_load((ROOT / "config/evals.yaml").read_text())
    for key, entry in raw.items():
        if isinstance(entry, dict):
            assert {"source", "status"} & set(entry), key
    assert CFG.thresholds["precision_grade_a"].value == 0.95  # ARCH initial values
    assert CFG.thresholds["citation_validity"].value == 0.97
    raw["alpha"] = {"value": 2, "status": "x"}
    with pytest.raises(EvalConfigInvalid):
        build(raw, b"x")


def test_metric_known_answers() -> None:
    assert metrics.coverage(["a", "b", "c", "d"], ["a", "c", "z"]).value == 0.5
    assert metrics.high_row_coverage({"r1": "done", "r2": "none", "r3": "blocked"}).value == 2 / 3
    cit = [
        metrics.Citation(True, True),
        metrics.Citation(True, False),
        metrics.Citation(False, True),
        metrics.Citation(True, True),
    ]
    assert metrics.citation_validity(cit) == metrics.Ratio(2, 4)
    acc = metrics.grade_accuracy(
        [
            ("confirmed", "confirmed"),
            ("confirmed", "probable"),
            ("probable", "probable"),
            ("lead", "confirmed"),
        ]
    )
    assert acc.precision["confirmed"] == metrics.Ratio(1, 2)
    assert acc.recall["confirmed"] == metrics.Ratio(1, 2)
    assert acc.precision["probable"].value == 1.0 and acc.confusion[("confirmed", "lead")] == 1
    # bins of width 0.5: [0.2 wrong, 0.4 right]: acc .5, conf .3;
    # [0.9 right, 1.0 right]: acc 1, conf .95
    ece = metrics.calibration_error([(0.2, False), (0.4, True), (0.9, True), (1.0, True)], 2)
    assert ece == pytest.approx(0.5 * 0.2 + 0.5 * 0.05)
    assert metrics.calibration_error([], 10) is None
    assert metrics.unsupported_rate([True, False, False, True]).value == 0.5
    assert metrics.run_cost([Decimal("0.1"), Decimal("0.2")]) == Decimal("0.3")
    assert metrics.run_cost([Decimal("0.1"), None]) is None  # unknown is never zero
    assert metrics.p95(list(range(1, 101))) == 95 and metrics.p95([]) is None
    assert metrics.Ratio(0, 0).value is None


@given(
    st.lists(st.tuples(st.floats(0, 1), st.booleans()), max_size=30),
    st.randoms(use_true_random=False),
)
def test_metrics_are_invariant_to_case_order(items: list, rnd: random.Random) -> None:
    shuffled = items[:]
    rnd.shuffle(shuffled)
    a = metrics.calibration_error(items, 10)
    b = metrics.calibration_error(shuffled, 10)
    assert (a is None and b is None) or a == pytest.approx(b)


def test_statistics_against_reference_values() -> None:
    lo, hi = stats.wilson(5, 10)  # type: ignore[misc]
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)
    lo, hi = stats.wilson(0, 20)  # type: ignore[misc]
    assert lo == 0.0 and round(hi, 4) == 0.1611
    assert stats.wilson(0, 0) is None
    # Holm: p sorted .01 .02 .03 .04 at alpha .05: thresholds .0125 .0167 .025 .05
    assert stats.holm({"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04}, 0.05) == {"a"}
    assert stats.holm({"a": 0.01, "b": 0.012, "c": 0.02, "d": 0.04}, 0.05) == {"a", "b", "c", "d"}
    assert stats.holm({}, 0.05) == frozenset()
    worse = stats.paired_bootstrap([1.0] * 20, [0.0] * 20, 500, 1)
    assert worse < 0.01
    assert stats.paired_bootstrap([0.5] * 5, [0.6] * 5, 500, 1) == 1.0
    assert stats.paired_bootstrap([0.5, 0.4], [0.5, 0.4], 100, 3) == 1.0
    with pytest.raises(ValueError):
        stats.paired_bootstrap([1.0], [1.0, 2.0], 10, 1)


@given(
    st.integers(0, 50),
    st.integers(1, 50),
    st.lists(st.floats(0, 1), min_size=1, max_size=8),
    st.integers(0, 9),
)
def test_statistics_stay_inside_their_bounds(k: int, n: int, xs: list[float], seed: int) -> None:
    k = min(k, n)
    lo, hi = stats.wilson(k, n)  # type: ignore[misc]
    assert 0.0 <= lo <= k / n <= hi <= 1.0
    p = stats.paired_bootstrap(xs, [x * 0.9 for x in xs], 50, seed)
    assert 0.0 < p <= 1.0
    assert stats.holm({f"m{i}": x for i, x in enumerate(xs)}, 0.05) <= {
        f"m{i}" for i in range(len(xs))
    }


def good(**over: float | None) -> dict[str, float | None]:
    base: dict[str, float | None] = {
        "precision_grade_a": 0.97,
        "precision_grade_b": 0.9,
        "coverage": 0.85,
        "citation_validity": 0.99,
        "calibration_error": 0.03,
        "security_pass_rate": 1.0,
        "cost_per_run": 1.0,
        "p95_duration_s": 100.0,
        "unsupported_claim_rate": 0.02,
    }
    base.update(over)
    return base


RAN = {"replay": "ran"}


def test_gate_blocks_every_failure_class_and_passes_a_clean_candidate() -> None:
    ok = gates.evaluate(CFG, "pull_request", RAN, good(), good(), {})
    assert ok.status == "pass" and ok.reasons == ()
    cases = [
        ({"replay": "not_run"}, good(), "mode replay: not_run"),
        ({"replay": "budget_exceeded"}, good(), "budget_exceeded"),
        ({"replay": "infrastructure_error"}, good(), "infrastructure_error"),
        ({"replay": "timeout"}, good(), "timeout"),
        (RAN, good(security_pass_rate=0.99), "security"),
        (RAN, good(precision_grade_a=0.9), "precision_grade_a"),
        (RAN, good(citation_validity=None), "citation_validity: no data"),
        (RAN, good(cost_per_run=1.2), "cost_vs_baseline"),
    ]
    for modes, cand, needle in cases:
        r = gates.evaluate(CFG, "pull_request", modes, cand, good(), {})
        assert r.status == "block" and any(needle in x for x in r.reasons), (needle, r.reasons)
    live = gates.evaluate(CFG, "prompt_or_model_change", RAN, good(), good(), {})
    assert any("mode live: not_run" in r for r in live.reasons)  # E5: no override
    na = gates.evaluate(CFG, "pull_request", RAN, good(), None, {})
    assert na.status == "pass" and "cost_vs_baseline" in na.not_applicable
    flaky = gates.evaluate(
        CFG,
        "pull_request",
        RAN,
        good(),
        good(),
        {},
        gates.flaky_metrics({"coverage": [[0.8, 0.9]]}),
    )
    assert flaky.status == "block" and gates.flaky_metrics({"coverage": [[0.8, 0.8]]}) == set()


def _cases(rnd: random.Random, n: int, mean: float, sd: float) -> list[float]:
    return [min(1.0, max(0.0, rnd.gauss(mean, sd))) for _ in range(n)]


def test_a_a_runs_block_less_often_than_the_configured_rate() -> None:
    """E10: identical candidate and baseline distributions, many seeds."""
    cfg = replace(CFG, bootstrap_resamples=300)
    blocks = 0
    trials = 100
    for seed in range(trials):
        rnd = random.Random(seed)
        paired = {
            m: (_cases(rnd, 30, 0.85, 0.05), _cases(rnd, 30, 0.85, 0.05))
            for m in cfg.primary_metrics
        }
        if gates.evaluate(cfg, "pull_request", RAN, good(), good(), paired).status == "block":
            blocks += 1
    assert blocks / trials <= cfg.a_a_false_block_rate_max


@pytest.mark.parametrize(
    ("metric", "shift"),
    [
        ("coverage", -0.15),  # a degraded prompt finds less
        ("citation_validity", -0.2),  # a dropped citation or a broken quote check
        ("precision_grade_a", -0.2),  # an inflated grade
        ("unsupported_claim_rate", +0.2),  # a task routed to a weaker candidate
    ],
)
def test_seeded_regressions_block(metric: str, shift: float) -> None:
    rnd = random.Random(7)
    base = _cases(rnd, 30, 0.6, 0.05)
    cand = [min(1.0, max(0.0, x + shift)) for x in base]
    r = gates.evaluate(CFG, "pull_request", RAN, good(), good(), {metric: (base, cand)})
    assert r.status == "block" and any(metric in x for x in r.reasons)
    better = [min(1.0, max(0.0, x - shift)) for x in base]
    assert (
        gates.evaluate(CFG, "pull_request", RAN, good(), good(), {metric: (base, better)}).status
        == "pass"
    )  # an improvement passes


def test_golden_loader_refuses_incomplete_labels_and_counts_stale_ones(tmp_path: Path) -> None:
    gs = golden.load(sorted(LABELS.glob("*.yaml")), date(2026, 10, 6), 92)
    assert gs.stale_excluded == 0 and len(gs.labels()) == 2 and len(gs.labels("holdout")) == 1
    later = golden.load(sorted(LABELS.glob("*.yaml")), date(2027, 6, 1), 92)
    assert later.stale_excluded == 2 and later.labels() == []
    raw = yaml.safe_load((LABELS / "synthetic-acme.yaml").read_text())
    for field, value in (
        ("captured_on", None),
        ("split", None),
        ("analyst", "Jane Smith"),
        ("source_url", None),
    ):
        bad = json.loads(json.dumps(raw, default=str))
        if value is None:
            del bad["labels"][0][field]
        else:
            bad["labels"][0][field] = value
        (tmp_path / "x.yaml").write_text(yaml.safe_dump(bad))
        with pytest.raises(golden.GoldenInvalid):
            golden.load([tmp_path / "x.yaml"], date(2026, 10, 6), 92)


def test_a_label_planted_in_a_prompt_fails_the_contamination_check(tmp_path: Path) -> None:
    values = [
        lb.value
        for _, lb in golden.load(sorted(LABELS.glob("*.yaml")), date(2026, 10, 6), 92).labels()
    ]
    prompts = list((ROOT / "src/account_intelligence/agents/prompts").glob("*.md"))
    prompts += list((ROOT / "src/verify/prompts").glob("*.md"))
    assert contamination.scan(prompts, values) == []  # the real prompts are clean
    planted = tmp_path / "planner.v9.md"
    planted.write_text("Example: the answer is Senior Salesforce Administrator.")
    assert contamination.scan([planted], values) != []
    assert cli(["contamination", "--labels", str(LABELS), "--scan", str(tmp_path)]) == 1


def test_an_uncalibrated_judge_never_gates() -> None:
    good_judge = calibrate.calibrate([True, False, True, True], [True, False, True, True], 0.8)
    assert good_judge.may_gate and good_judge.agreement == 1.0 and good_judge.kappa == 1.0
    bad = calibrate.calibrate([True, True, True, False], [False, True, False, True], 0.8)
    assert not bad.may_gate and bad.agreement == 0.25
    assert not calibrate.calibrate([], [], 0.8).may_gate


def test_security_evals_are_zero_tolerance() -> None:
    clean = adversarial.check(
        ["a brief"], ["CANARY-7f3a9e"], {"tech.crm": ("lead", "lead")}, ["TENANT-B-MARK"]
    )
    assert clean.pass_rate == 1.0
    leak = adversarial.check(["text with canary-7f3a9e inside"], ["CANARY-7f3a9e"], {}, [])
    raised = adversarial.check([], [], {"tech.crm": ("lead", "confirmed")}, [])
    cross = adversarial.check(["TENANT-B-MARK"], [], {}, ["TENANT-B-MARK"])
    for r in (leak, raised, cross):
        assert r.pass_rate is not None and r.pass_rate < 1.0
        assert (
            gates.evaluate(
                CFG, "pull_request", RAN, good(security_pass_rate=r.pass_rate), good(), {}
            ).status
            == "block"
        )


def _result():  # type: ignore[no-untyped-def]
    acct = golden.load(sorted(LABELS.glob("*.yaml")), date(2026, 10, 6), 92).accounts[0]
    out = CaseOutput(
        facts={
            "jobs.salesforce_admin.open": ("Senior Salesforce Administrator", "confirmed", 0.95)
        },
        citations=(metrics.Citation(True, True),),
        supported=(True, False),
        high_rows={"job_postings_12_months": "done"},
        costs=(Decimal("0.5"),),
        duration_s=60.0,
        security_failures=0,
        security_checks=3,
    )
    return aggregate([(acct, 0, out), (acct, 1, out)], 10)


def test_runner_aggregates_cases_exactly() -> None:
    r = _result()
    assert r.metrics["coverage"] == 0.5 and r.bases["coverage"] == (2, 4)
    assert r.metrics["precision_grade_a"] == 1.0 and r.metrics["unsupported_claim_rate"] == 0.5
    assert r.metrics["cost_per_run"] == 0.5 and r.metrics["security_pass_rate"] == 1.0
    assert r.metrics["calibration_error"] == pytest.approx(0.05)
    b, c = paired_scores(r, r, "coverage")
    assert b == c == [0.5, 0.5]


def test_baselines_are_immutable(tmp_path: Path) -> None:
    r = _result()
    path = write_new(tmp_path, r, PROV)
    with pytest.raises(BaselineExists):
        write_new(tmp_path, r, PROV)
    back, prov = from_json(json.loads(path.read_text()))
    assert back.metrics == r.metrics and prov == PROV and to_json(back, prov) == to_json(r, PROV)


def test_report_is_deterministic_and_states_the_verdict(tmp_path: Path) -> None:
    r = _result()
    verdict = gates.evaluate(CFG, "pull_request", RAN, r.metrics, None, {})
    one = report.markdown(r, verdict, PROV, CFG.alpha)
    assert one == report.markdown(r, verdict, PROV, CFG.alpha)
    assert one.startswith("# Eval report: **BLOCK**")  # synthetic data fails the thresholds
    assert report.as_json(r, verdict, PROV) == report.as_json(r, verdict, PROV)
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps(to_json(r, PROV)))
    assert (
        cli(
            [
                "gate",
                "--candidate",
                str(cand),
                "--modes",
                "replay=ran",
                "--out",
                str(tmp_path / "r.md"),
            ]
        )
        == 1
    )
    assert (tmp_path / "r.md").read_text().startswith("# Eval report")


def test_bench_template_is_blocked_without_inputs(tmp_path: Path) -> None:
    pairs = bench.matrix(
        ["extract_facts", "write_narrative"],
        {"extract_facts": ["fast", "strong"], "write_narrative": ["strong"]},
    )
    assert pairs == [
        ("extract_facts", "fast"),
        ("extract_facts", "strong"),
        ("write_narrative", "strong"),
    ]
    text = bench.template("b-001", pairs, "needs golden accounts")
    assert (
        "Status: **Blocked: needs golden accounts**" in text
        and "Proposed routing diff: none" in text
    )
    with pytest.raises(ValueError):
        bench.matrix(
            [f"t{i}" for i in range(20)], {f"t{i}": ["a", "b", "c", "d"] for i in range(20)}
        )
    assert cli(["bench", "--id", "b-002", "--out", str(tmp_path / "b.md")]) == 0
