"""The merge gate (E4 to E7). Pure: a candidate blocks when

* a required mode did not run cleanly (``not_run``, ``budget_exceeded``,
  ``infrastructure_error``, ``timeout``): there is no override in code (E5);
* the security pass rate is below 1.0 (injection, leakage, cross-tenant: zero tolerance, E7);
* any absolute threshold fails, or a gated metric that must be measured has no data;
* a primary metric shows a significant paired drop against the baseline after Holm correction;
* a metric is flaky (identical input, different values across repeats, E6).

Cost and p95 duration are compared with the baseline as ratios; without a baseline, or in replay
mode for duration, they are ``not_applicable`` (recorded, not silently passed as measured).

GUARANTEE(test_gate_blocks_every_failure_class_and_passes_a_clean_candidate): E4, E5, E7.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from evalgate.config import EvalConfig
from evalgate.stats import holm, paired_bootstrap

CLEAN = "ran"
RELATIVE = {"cost_vs_baseline": "cost_per_run", "p95_duration_vs_baseline": "p95_duration_s"}
LOWER_IS_BETTER = frozenset({"unsupported_claim_rate", "calibration_error"})


@dataclass(frozen=True)
class GateResult:
    """The verdict and every reason."""

    status: str  # pass or block
    reasons: tuple[str, ...]
    not_applicable: tuple[str, ...] = ()
    pvalues: Mapping[str, float] = field(default_factory=dict)


def flaky_metrics(
    repeats: Mapping[str, Sequence[Sequence[float]]], tolerance: float = 0.0
) -> frozenset[str]:
    """Metrics whose per-case repeats on identical input differ by more than ``tolerance``."""
    return frozenset(
        m
        for m, cases in repeats.items()
        if any(rs and max(rs) - min(rs) > tolerance for rs in cases)
    )


def evaluate(
    cfg: EvalConfig,
    trigger: str,
    modes: Mapping[str, str],
    candidate: Mapping[str, float | None],
    baseline: Mapping[str, float | None] | None,
    paired: Mapping[str, tuple[Sequence[float], Sequence[float]]],
    flaky: frozenset[str] = frozenset(),
) -> GateResult:
    """See the module docstring. ``paired[m] = (baseline_cases, candidate_cases)``."""
    reasons: list[str] = []
    na: list[str] = []
    for mode in cfg.required_modes.get(trigger, ("replay",)):
        status = modes.get(mode, "not_run")
        if status != CLEAN:
            reasons.append(f"mode {mode}: {status}")
    security = candidate.get("security_pass_rate")
    if security is None or security < 1.0:
        reasons.append("security evals below 100%")
    for name, threshold in cfg.thresholds.items():
        if name == "security_pass_rate":
            continue
        if name in RELATIVE:
            base = (baseline or {}).get(RELATIVE[name])
            value = candidate.get(RELATIVE[name])
            if base in (None, 0) or value is None:
                na.append(name)
                continue
            if not threshold.holds(value / base):
                reasons.append(f"{name}: {value / base:.3f} fails {threshold.op} {threshold.value}")
            continue
        value = candidate.get(name)
        if value is None:
            reasons.append(f"{name}: no data")
        elif not threshold.holds(value):
            reasons.append(f"{name}: {value:.4f} fails {threshold.op} {threshold.value}")
    pvalues: dict[str, float] = {}
    for metric in cfg.primary_metrics:
        if metric not in paired:
            continue
        base_cases, cand_cases = paired[metric]
        if metric in LOWER_IS_BETTER:  # a rise is the drop
            base_cases, cand_cases = [-x for x in base_cases], [-x for x in cand_cases]
        pvalues[metric] = paired_bootstrap(
            base_cases, cand_cases, cfg.bootstrap_resamples, cfg.seed
        )
    for metric in sorted(holm(pvalues, cfg.alpha)):
        reasons.append(f"{metric}: significant drop against the baseline (Holm)")
    for metric in sorted(flaky):
        reasons.append(f"{metric}: flaky on identical input")
    return GateResult("block" if reasons else "pass", tuple(reasons), tuple(sorted(na)), pvalues)
