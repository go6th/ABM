#!/usr/bin/env python3
"""T9 golden-set labelling helper (v0).

Supports the protocol in docs/golden_set/protocol.md:

- ``init``      copy the template fixture to a new slot file with agreement placeholder
- ``agreement`` compute two-labeller agreement for a slot from two label files
- ``validate``  validate a fixture against the schema (always; used by CI)

Exit codes: 0 ok, 1 validation/protocol failure.

Usage:
  python scripts/label_golden_set.py validate fixtures/golden_v0/account_template.json
  python scripts/label_golden_set.py init G-2 fixtures/golden_v0/g2.json
  python scripts/label_golden_set.py agreement a.json b.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs" / "golden_set" / "schema.json"
TEMPLATE = ROOT / "fixtures" / "golden_v0" / "account_template.json"

VALID_GRADES = ("Confirmed", "Probable", "Hypothesis", "Lead")
ROW_IDS = {f"C{i:02d}" for i in range(1, 34)}
AGREEMENT_THRESHOLD = 0.80


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return doc


def validate_schema(doc: dict[str, Any]) -> list[str]:
    """Structural validation mirroring schema.json (no external jsonschema dependency)."""
    errors: list[str] = []
    slot = doc.get("slot")
    if not isinstance(slot, str) or not re.fullmatch(r"G-[1-9]", slot):
        errors.append(f"slot must match G-1..G-9, got {slot!r}")
    if doc.get("schema_version") != "0.1":
        errors.append("schema_version must be '0.1'")
    if not doc.get("captured_at"):
        errors.append("captured_at missing (ISO 8601 UTC expected)")
    labellers = doc.get("labelled_by")
    if not isinstance(labellers, list) or len(labellers) < 2:
        errors.append("labelled_by must list at least two independent labellers")

    rows = doc.get("rows")
    if not isinstance(rows, list) or len(rows) != 33:
        n = len(rows) if isinstance(rows, list) else "n/a"
        errors.append(f"rows must contain exactly 33 items, got {n}")
        return errors

    seen: list[str] = []
    for row in rows:
        rid = row.get("row_id", "?") if isinstance(row, dict) else "?"
        seen.append(str(rid))
        grade = row.get("grade")
        if grade not in VALID_GRADES:
            errors.append(f"{rid}: invalid grade {grade!r}")
        conf = row.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0.0 <= conf <= 1.0:
            errors.append(f"{rid}: confidence must be a number in [0,1], got {conf!r}")
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"{rid}: evidence must be a list")
        elif grade == "Confirmed" and not evidence:
            errors.append(f"{rid}: Confirmed rows require at least one evidence entry")
    dupes = {rid for rid in seen if seen.count(rid) > 1}
    if dupes:
        errors.append(f"duplicate row ids: {sorted(dupes)}")
    missing = ROW_IDS - set(seen)
    if missing:
        errors.append(f"missing coverage rows: {sorted(missing)}")
    return errors


def cmd_validate(path: Path) -> int:
    try:
        doc = _load(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: cannot load {path}: {exc}")
        return 1
    errors = validate_schema(doc)
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print(f"T9 golden fixture: OK ({path.name}: 33 rows, grades valid, Confirmed rows evidenced)")
    return 0


def cmd_init(slot: str, dest: Path) -> int:
    if not TEMPLATE.exists():
        print(f"FAIL: template missing at {TEMPLATE}")
        return 1
    doc = _load(TEMPLATE)
    doc["slot"] = slot
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"initialised {dest} (slot={slot}); replace notes and evidence per protocol")
    return 0


def cmd_agreement(path_a: Path, path_b: Path) -> int:
    doc_a, doc_b = _load(path_a), _load(path_b)
    rows_a = {r["row_id"]: r for r in doc_a.get("rows", [])}
    rows_b = {r["row_id"]: r for r in doc_b.get("rows", [])}
    shared = sorted(set(rows_a) & set(rows_b))
    if not shared:
        print("FAIL: no overlapping row ids between the two label files")
        return 1
    matches = sum(1 for rid in shared if rows_a[rid].get("grade") == rows_b[rid].get("grade"))
    agreement = matches / len(shared)
    disagreements = [rid for rid in shared if rows_a[rid].get("grade") != rows_b[rid].get("grade")]
    print(f"agreement over {len(shared)} rows: {agreement:.2f} ({matches} exact matches)")
    if disagreements:
        print(f"adjudicate: {', '.join(disagreements)}")
    if agreement < AGREEMENT_THRESHOLD:
        print(f"FAIL: agreement {agreement:.2f} < required {AGREEMENT_THRESHOLD:.2f}")
        return 1
    print("T9 agreement: OK (>= 0.80)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "validate":
        return cmd_validate(Path(args[1]))
    if len(args) == 3 and args[0] == "init":
        return cmd_init(args[1], Path(args[2]))
    if len(args) == 3 and args[0] == "agreement":
        return cmd_agreement(Path(args[1]), Path(args[2]))
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
