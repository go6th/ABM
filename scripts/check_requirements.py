#!/usr/bin/env python3
"""T3 exit-criterion checker: requirements baseline.

Fails (exit 1) unless requirements.csv:
- has at least 120 rows
- matches the schema header exactly
- has no duplicate req_id
- every row has req_id, title, acceptance_condition, owning_task, test_id, source, status
- every req_id uses a known prefix (REQ-BIZ, REQ-IN, REQ-COV, REQ-PPL, REQ-GRD, REQ-WRT,
  REQ-DEL, REQ-GATE, REQ-RULE, REQ-BRK, REQ-DAT, REQ-REF, REQ-FR, REQ-NFR, REQ-ISO,
  REQ-SEC)
- every owning_task references an existing task number (T1..T100)
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "docs" / "phase0" / "requirements.csv"

HEADER = ["req_id", "title", "acceptance_condition", "owning_task", "test_id", "source", "status"]
MIN_ROWS = 120
PREFIXES = (
    "REQ-BIZ-",
    "REQ-IN-",
    "REQ-COV-",
    "REQ-PPL-",
    "REQ-GRD-",
    "REQ-WRT-",
    "REQ-DEL-",
    "REQ-GATE-",
    "REQ-RULE-",
    "REQ-BRK-",
    "REQ-DAT-",
    "REQ-REF-",
    "REQ-FR-",
    "REQ-NFR-",
    "REQ-ISO-",
    "REQ-SEC-",
)
TASK_RE = re.compile(r"^T(\d+)(,T(\d+))*$")
REQUIRED_FIELDS = ("title", "acceptance_condition", "owning_task", "test_id", "source", "status")


def _load_rows() -> tuple[list[dict[str, str]], str | None]:
    """Return (rows, error). error is None on success."""
    if not REQ.exists():
        return [], f"{REQ} missing"
    with REQ.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != HEADER:
            return [], f"header {reader.fieldnames} != {HEADER}"
        return list(reader), None


def _check_row(i: int, row: dict[str, str], seen: set[str], errors: list[str]) -> None:
    rid = (row.get("req_id") or "").strip()
    if not rid:
        errors.append(f"line {i}: empty req_id")
        return
    if rid in seen:
        errors.append(f"line {i}: duplicate req_id {rid}")
    seen.add(rid)
    if not rid.startswith(PREFIXES):
        errors.append(f"line {i}: {rid} uses an unknown prefix")
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            errors.append(f"line {i} ({rid}): missing {field}")
    tasks = (row.get("owning_task") or "").strip()
    if tasks and not TASK_RE.match(tasks):
        errors.append(f"line {i} ({rid}): owning_task format invalid: {tasks!r}")
    else:
        for num in re.findall(r"T(\d+)", tasks):
            if not 1 <= int(num) <= 100:
                errors.append(f"line {i} ({rid}): owning task T{num} out of range")


def main() -> int:
    rows, load_error = _load_rows()
    if load_error:
        print(f"FAIL: {load_error}")
        return 1

    errors: list[str] = []
    if len(rows) < MIN_ROWS:
        errors.append(f"rows = {len(rows)}, expected >= {MIN_ROWS}")

    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        _check_row(i, row, seen, errors)

    if errors:
        for err in errors[:40]:
            print(f"FAIL: {err}")
        print(f"TOTAL ERRORS: {len(errors)}")
        return 1
    print(f"T3 requirements baseline: OK ({len(rows)} REQ-IDs, all fields present, no duplicates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
