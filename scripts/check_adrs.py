#!/usr/bin/env python3
"""T8 exit-criterion checker: 16 ADRs, each with Status: Accepted.

Fails (exit 1) if any ADR file ADR-001..ADR-016 is missing, dropped, or its status is
not exactly 'Accepted'. Also validates that docs/adr/README.md lists all 16.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
EXPECTED = [f"ADR-{i:03d}" for i in range(1, 17)]


def main() -> int:
    errors: list[str] = []
    for adr in EXPECTED:
        matches = sorted(ADR_DIR.glob(f"{adr}-*.md"))
        if not matches:
            errors.append(f"{adr}: file missing")
            continue
        text = matches[0].read_text(encoding="utf-8")
        m = re.search(r"^Status:\s*(.+)$", text, re.MULTILINE)
        if not m:
            errors.append(f"{adr}: no Status line")
        elif m.group(1).strip() != "Accepted":
            errors.append(f"{adr}: status is {m.group(1).strip()!r}, expected 'Accepted'")

    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    for adr in EXPECTED:
        if adr not in index:
            errors.append(f"README.md: {adr} not listed")

    cost = (ADR_DIR / "cost_estimate_phase1.md").read_text(encoding="utf-8")
    if "$600" not in cost:
        errors.append("cost_estimate_phase1.md missing the $600/month cap reference")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("T8 ADRs: OK (16 ADRs, all Accepted, index complete, cost cap present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
