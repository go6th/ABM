#!/usr/bin/env python3
"""T4 exit-criterion checker: source-document conflict register.

Fails (exit 1) unless conflict_register.csv:
- contains exactly the 10 required conflicts C1..C10
- every row has a decision (or an explicit deferral with a revisit_trigger)
- every row references at least one owning task (T<number>)
- status is 'decided' or 'deferred-with-trigger'
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "docs" / "phase0" / "conflict_register.csv"
REQUIRED_IDS = {f"C{i}" for i in range(1, 11)}
TASK_RE = re.compile(r"T\d+")
VALID_STATUS = {"decided", "deferred-with-trigger"}


def main() -> int:
    if not REG.exists():
        print(f"FAIL: {REG} missing")
        return 1
    with REG.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    errors: list[str] = []
    ids = {r.get("id", "").strip() for r in rows}
    missing = REQUIRED_IDS - ids
    if missing:
        errors.append(f"missing conflicts: {sorted(missing)}")
    if len(rows) != 10:
        errors.append(f"rows = {len(rows)}, expected 10")

    for row in rows:
        rid = row.get("id", "?")
        decision = (row.get("decision") or "").strip()
        trigger = (row.get("revisit_trigger") or "").strip()
        status = (row.get("status") or "").strip().lower()
        tasks = (row.get("tasks_affected") or "").strip()
        if status not in VALID_STATUS:
            errors.append(f"{rid}: status must be decided|deferred-with-trigger, got {status!r}")
        if not decision:
            errors.append(f"{rid}: missing decision")
        if not tasks or not TASK_RE.search(tasks):
            errors.append(f"{rid}: no owning task referenced")
        if status == "deferred-with-trigger" and not trigger:
            errors.append(f"{rid}: deferral requires a revisit_trigger")
        if not (row.get("decider") or "").strip():
            errors.append(f"{rid}: missing decider")
        if not (row.get("date") or "").strip():
            errors.append(f"{rid}: missing decision date")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("T4 conflict register: OK (10 conflicts, all decided or deferred with trigger)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
