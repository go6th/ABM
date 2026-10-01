#!/usr/bin/env python3
"""T7 exit-criterion checker: verification register V1-V11.

Fails (exit 1) unless verification_register.csv:
- contains all 11 required rows V1..V11
- every row has owner and target_date (ISO date)
- every row has why_it_matters, source, status and consequence_if_wrong
- status is one of open|closed|accepted-risk

With --strict-open the script additionally fails while any row is 'open'
(use for the T14 gate: the gate records which opens were accepted).
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "docs" / "phase0" / "verification_register.csv"
REQUIRED_IDS = {f"V{i}" for i in range(1, 12)}
VALID_STATUS = {"open", "closed", "accepted-risk"}
REQUIRED_FIELDS = (
    "item",
    "why_it_matters",
    "owner",
    "target_date",
    "source",
    "status",
    "consequence_if_wrong",
)


def _check_row(row: dict[str, str], errors: list[str], open_items: list[str]) -> None:
    rid = row.get("id", "?")
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            errors.append(f"{rid}: missing {field}")
    status = (row.get("status") or "").strip().lower()
    if status not in VALID_STATUS:
        errors.append(f"{rid}: invalid status {status!r}")
    tdate = (row.get("target_date") or "").strip()
    if tdate:
        try:
            parsed = date.fromisoformat(tdate)
        except ValueError:
            errors.append(f"{rid}: target_date not ISO: {tdate!r}")
        else:
            if parsed < date(2026, 10, 1):
                errors.append(f"{rid}: target_date in the past ({tdate})")
    if status == "open":
        open_items.append(rid)


def main() -> int:
    strict_open = "--strict-open" in sys.argv
    if not REG.exists():
        print(f"FAIL: {REG} missing")
        return 1
    with REG.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    errors: list[str] = []
    ids = {r.get("id", "").strip() for r in rows}
    missing = REQUIRED_IDS - ids
    if missing:
        errors.append(f"missing register items: {sorted(missing)}")

    open_items: list[str] = []
    for row in rows:
        _check_row(row, errors, open_items)

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    if strict_open and open_items:
        print(f"FAIL (strict): open verification items remain: {sorted(open_items)}")
        return 1

    print(f"T7 verification register: OK (11 items, owners + dates set; open: {len(open_items)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
