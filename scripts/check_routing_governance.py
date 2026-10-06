"""R12 / D-51: a change to ``config/model_routing.yaml`` needs a recorded decision.

``docs/routing/DECISIONS.md`` must hold a line naming the file's current sha256 and either
``benchmark: <report id>`` (a benchmark report under ``docs/routing/``) or
``status: proposal-unapplied`` (a proposal that cannot go live: the container refuses
``MODEL_ROUTER=on`` while ``benchmark_report`` is null). Exit 1 otherwise. The eval gate is a
separate required check on the same pull request.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(root: Path = ROOT) -> list[str]:
    """Problems found (empty when governed)."""
    config = root / "config/model_routing.yaml"
    decisions = root / "docs/routing/DECISIONS.md"
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    if not decisions.exists():
        return ["docs/routing/DECISIONS.md is missing"]
    lines = [ln for ln in decisions.read_text().splitlines() if digest in ln]
    if not lines:
        return [f"no decision names the routing config {digest[:12]}"]
    problems: list[str] = []
    for line in lines:
        bench = re.search(r"benchmark:\s*(benchmark-[\w.-]+)", line)
        if bench:
            if not (root / "docs/routing" / f"{bench.group(1)}.md").exists():
                problems.append(f"benchmark report {bench.group(1)} does not exist")
            continue
        if "status: proposal-unapplied" not in line:
            problems.append("the decision names neither a benchmark nor proposal-unapplied")
    return problems


def main() -> int:
    """Print problems; exit 1 when any."""
    problems = check()
    for p in problems:
        print(f"routing governance: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
