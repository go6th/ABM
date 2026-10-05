"""Model split benchmark harness (P0-2, roadmap s6): one model for everything versus a split by
task, over the same golden accounts.

Input: one JSON file per (arm, account) run with the run's statuses, graded facts, people and token
usage per tier (``LlmResult.usage``, recorded by the runner). Output: a table per arm with the
golden-account scores (every figure with its base) and tokens per tier. Cost stays blank while a
price is missing in ``config/models.yaml`` (prices are not guessed). With no analyst-reviewed
golden accounts the report says Blocked.

    python -m evals.model_benchmark RUNS_DIR evals/golden/accounts > docs/benchmarks/model-split.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.golden_accounts import AccountScore, load_accounts, score_run


def _ratio(pairs: list[tuple[int, int]]) -> str:
    got, base = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
    return f"{got} of {base}"


def report(runs: list[dict[str, Any]], golden_dir: Path) -> str:
    """The markdown results table."""
    golden = {g.account_domain: g for g in load_accounts(golden_dir)}
    by_arm: dict[str, list[AccountScore]] = defaultdict(list)
    tokens: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for run in runs:
        g = golden.get(run["account_domain"])
        if g is None:
            continue
        by_arm[run["arm"]].append(score_run(g, run["statuses"], run["facts"], run["people"]))
        for tier, n in run.get("tokens", {}).items():
            tokens[run["arm"]][tier] += int(n)
    real = sum(not g.synthetic for g in golden.values())
    lines = ["# Model split benchmark (P0-2)", ""]
    if real < 3:
        lines += [
            "**Blocked: needs analyst-reviewed golden accounts and the September account",
            "outputs.** Rows below come from synthetic fixtures only and are not results.",
            "",
        ]
    lines += [
        "| Arm | Accounts | Coverage match | Attributes found | Grade acceptable |"
        " People found | Tokens by tier | Cost |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm, scores in sorted(by_arm.items()):
        tok = ", ".join(f"{t} {n}" for t, n in sorted(tokens[arm].items())) or "not recorded"
        lines.append(
            f"| {arm} | {len(scores)} | {_ratio([s.coverage_match for s in scores])} |"
            f" {_ratio([s.attributes_found for s in scores])} |"
            f" {_ratio([s.attributes_grade_ok for s in scores])} |"
            f" {_ratio([s.people_found for s in scores])} | {tok} | blank: no confirmed price |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Print the report for a directory of run files."""
    runs = [json.loads(p.read_text()) for p in sorted(Path(argv[0]).glob("*.json"))]
    sys.stdout.write(report(runs, Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
