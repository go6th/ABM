"""Deterministic markdown and JSON eval reports (D-50). The same inputs give the same bytes.

GUARANTEE(test_report_is_deterministic_and_states_the_verdict): D-50.
"""

from __future__ import annotations

import json
from typing import Any

from evalgate.baseline import Provenance, to_json
from evalgate.gates import GateResult
from evalgate.runner import EvalResult
from evalgate.stats import wilson


def markdown(result: EvalResult, gate: GateResult, prov: Provenance, alpha: float) -> str:
    """The PR comment and artifact text."""
    lines = [
        f"# Eval report: **{gate.status.upper()}**",
        "",
        f"- mode: {prov.mode}; code {prov.code_sha[:12]}; golden {prov.golden_version[:12]}",
        f"- prompts {prov.prompts_hash[:12]}; routing {prov.routing_hash[:12]}",
        f"- stale labels excluded: {result.excluded_stale_labels}",
        "",
        "| metric | value | base | 95% interval |",
        "| --- | --- | --- | --- |",
    ]
    for name, value in sorted(result.metrics.items()):
        base = result.bases.get(name)
        interval = wilson(*base, alpha=alpha) if base else None
        lines.append(
            f"| {name} | {'n/a' if value is None else f'{value:.4f}'} | "
            f"{'' if base is None else f'{base[0]}/{base[1]}'} | "
            f"{'' if interval is None else f'{interval[0]:.3f} to {interval[1]:.3f}'} |"
        )
    lines += ["", "## Reasons", ""]
    lines += [f"- {r}" for r in gate.reasons] or ["- none"]
    if gate.not_applicable:
        lines += ["", "Not applicable: " + ", ".join(gate.not_applicable)]
    return "\n".join(lines) + "\n"


def as_json(result: EvalResult, gate: GateResult, prov: Provenance) -> str:
    """The machine-readable report."""
    body: dict[str, Any] = to_json(result, prov)
    body["gate"] = {
        "status": gate.status,
        "reasons": list(gate.reasons),
        "not_applicable": list(gate.not_applicable),
        "pvalues": dict(sorted(gate.pvalues.items())),
    }
    return json.dumps(body, sort_keys=True, indent=1) + "\n"
