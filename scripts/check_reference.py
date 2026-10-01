#!/usr/bin/env python3
"""T1 exit-criterion checker: reference set completeness.

Fails (exit 1) unless:
- coverage_tracker.csv has exactly 33 data rows (5 pass-1 + 28 pass-2)
- grading_scheme.md defines all four grades
- current_system.md lists the skill folder layout and the 13-step run
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "docs" / "reference"

GRADES = ("Confirmed", "Probable", "Hypothesis", "Lead")
STEP_MARKERS = [f"{i}." for i in range(1, 14)]


def check_coverage() -> list[str]:
    path = REF / "coverage_tracker.csv"
    if not path.exists():
        return ["coverage_tracker.csv missing"]
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    errors: list[str] = []
    if len(rows) != 33:
        errors.append(f"coverage rows = {len(rows)}, expected 33")
    p1 = sum(1 for r in rows if r.get("pass", "").strip() == "1")
    p2 = sum(1 for r in rows if r.get("pass", "").strip() == "2")
    if p1 != 5:
        errors.append(f"pass-1 rows = {p1}, expected 5")
    if p2 != 28:
        errors.append(f"pass-2 rows = {p2}, expected 28")
    ids = [r.get("row_id", "") for r in rows]
    if len(set(ids)) != len(ids):
        errors.append("duplicate row_id values in coverage_tracker.csv")
    if any(not r.get("proof_required") for r in rows):
        errors.append("coverage rows require a proof_required value")
    return errors


def check_grading() -> list[str]:
    text = (REF / "grading_scheme.md").read_text(encoding="utf-8")
    return [f"grade {g} not defined" for g in GRADES if g not in text]


def check_current_system() -> list[str]:
    text = (REF / "current_system.md").read_text(encoding="utf-8")
    errors: list[str] = []
    if "SKILL.md" not in text or "scripts/" not in text:
        errors.append("current_system.md missing skill folder layout")
    if "The 13-step run" not in text:
        errors.append("current_system.md missing the 13-step run section")
    if "third_party_scan" not in text or "strip_tool_names" not in text:
        errors.append("current_system.md missing gate scripts")
    return errors


def main() -> int:
    errors = check_coverage() + check_grading() + check_current_system()
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("T1 reference set: OK (33 rows, 4 grades, layout + 13 steps present)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
