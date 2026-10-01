#!/usr/bin/env python3
"""Phase-0 document presence/count checker (T2, T5, T6, T14).

Fails (exit 1) unless:
- attack_scenarios.csv covers A1..A9 and risk_register.csv covers R1..R10 (T2)
- product_owner_decisions.md contains PO-1..PO-10, a sign-off line each and the
  70% margin recompute (T5)
- legal_briefing.md, subprocessor_register.csv and dpa_template.md exist; the DPA
  keeps the counsel memo placeholder (T6)
- gate_review.md records every task T1..T14 with a status and has sign-off lines (T14)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P0 = ROOT / "docs" / "phase0"
LEGAL = ROOT / "docs" / "legal"


def ids_present(path: Path, prefix: str, expected: int) -> list[str]:
    if not path.exists():
        return [f"{path.name} missing"]
    with path.open(newline="", encoding="utf-8") as fh:
        ids = {row[0].strip() for row in csv.reader(fh) if row and row[0].strip()}
    missing = {f"{prefix}{i}" for i in range(1, expected + 1)} - ids
    return [f"{path.name}: missing {sorted(missing)}"] if missing else []


def check_t2() -> list[str]:
    return (
        ids_present(P0 / "attack_scenarios.csv", "A", 9)
        + ids_present(P0 / "risk_register.csv", "R", 10)
        + ([] if (P0 / "spec_review.md").exists() else ["spec_review.md missing"])
    )


def check_t5() -> list[str]:
    path = P0 / "product_owner_decisions.md"
    if not path.exists():
        return ["product_owner_decisions.md missing"]
    text = path.read_text(encoding="utf-8")
    errors = [f"PO-{i} missing" for i in range(1, 11) if f"PO-{i}" not in text]
    if text.count("Sign-off:") < 10:
        errors.append("expected a Sign-off line for each of PO-1..PO-10")
    if "70%" not in text:
        errors.append("70% margin recompute not documented")
    return errors


def check_t6() -> list[str]:
    errors: list[str] = []
    for name in ("legal_briefing.md", "subprocessor_register.csv", "dpa_template.md"):
        if not (LEGAL / name).exists():
            errors.append(f"docs/legal/{name} missing")
    dpa = LEGAL / "dpa_template.md"
    if dpa.exists():
        text = dpa.read_text(encoding="utf-8")
        if "COUNSEL MEMO" not in text:
            errors.append("dpa_template.md: counsel memo section missing")
        if "Date: ____" not in text:
            errors.append("dpa: counsel memo date must stay a blank placeholder")
    return errors


def check_t14() -> list[str]:
    path = P0 / "gate_review.md"
    if not path.exists():
        return ["gate_review.md missing"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    for task in range(1, 15):
        if f"T{task}" not in text:
            errors.append(f"gate_review.md: T{task} not covered")
    if "PASS" not in text or "BLOCKED" not in text:
        errors.append("gate_review.md: per-spike PASS/FAIL statuses not recorded")
    if text.count("Date:") < 2:
        errors.append("gate_review.md: sign-off date lines missing")
    return errors


def main() -> int:
    errors = check_t2() + check_t5() + check_t6() + check_t14()
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("Phase0 docs: OK (T2/T5/T6/T14 artefacts present and structured)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
