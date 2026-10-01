#!/usr/bin/env python3
"""T9 exit-criterion checker.

Fails (exit 1) unless:
- docs/golden_set/protocol.md covers selection, labelling, agreement and re-verification
- docs/golden_set/schema.json is present and parseable JSON
- fixtures/golden_v0/account_template.json exists and validates against the schema rules
- the labelling helper self-tests (agreement metric passes on a synthetic pair)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from label_golden_set import (  # noqa: E402
    AGREEMENT_THRESHOLD,
    cmd_agreement,
    cmd_validate,
    validate_schema,
)

ROOT_MARKER_SECTIONS = ("selection", "labelling", "agreement", "re-verification")
TEMPLATE = ROOT / "fixtures" / "golden_v0" / "account_template.json"


def check_protocol() -> list[str]:
    text = (ROOT / "docs" / "golden_set" / "protocol.md").read_text(encoding="utf-8")
    return [
        f"protocol.md missing section on {topic}"
        for topic in ROOT_MARKER_SECTIONS
        if topic not in text.lower()
    ]


def check_schema() -> list[str]:
    try:
        raw = (ROOT / "docs" / "golden_set" / "schema.json").read_text(encoding="utf-8")
        schema = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema.json unreadable: {exc}"]
    errors: list[str] = []
    if schema.get("title") != "Golden-set labelled account":
        errors.append("schema.json: unexpected title")
    rows = schema.get("properties", {}).get("rows", {})
    if rows.get("minItems") != 33 or rows.get("maxItems") != 33:
        errors.append("schema.json: rows must be pinned to exactly 33 items")
    grade_items = rows.get("items", {}).get("properties", {})
    grade_enum = grade_items.get("grade", {}).get("enum", [])
    if set(grade_enum) != {"Confirmed", "Probable", "Hypothesis", "Lead"}:
        errors.append("schema.json: grade enum must be the four labels")
    return errors


def check_template() -> list[str]:
    try:
        doc = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"account_template.json unreadable: {exc}"]
    return validate_schema(doc)


def check_helper_selftest() -> list[str]:
    """The labelling helper must agree with itself (agreement 1.0) and reject drift."""
    with TEMPLATE.open(encoding="utf-8") as fh:
        template = json.load(fh)
    twin = json.loads(json.dumps(template))
    twin_path = TEMPLATE.with_name("_selftest_twin.json")
    twin_path.write_text(json.dumps(twin), encoding="utf-8")
    try:
        if cmd_agreement(TEMPLATE, twin_path) != 0:
            return ["selftest: identical fixtures failed the agreement check"]
        drifted = json.loads(json.dumps(template))
        # Flip 8 of 33 rows: 25/33 ≈ 0.76 < 0.80, so the agreement gate must fail.
        for row in drifted["rows"][:8]:
            row["grade"] = "Lead"
        drifted_path = TEMPLATE.with_name("_selftest_drift.json")
        drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
        if cmd_agreement(TEMPLATE, drifted_path) == 0:
            return ["selftest: drifted fixture unexpectedly passed the agreement check"]
    finally:
        twin_path.unlink(missing_ok=True)
        TEMPLATE.with_name("_selftest_drift.json").unlink(missing_ok=True)
    if cmd_validate(TEMPLATE) != 0:
        return ["selftest: cmd_validate rejected the template fixture"]
    if validate_schema({}) == []:
        return ["selftest: validate_schema accepted an empty document"]
    if AGREEMENT_THRESHOLD != 0.80:
        return ["selftest: agreement threshold must be 0.80"]
    return []


def main() -> int:
    errors = check_protocol() + check_schema() + check_template() + check_helper_selftest()
    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("T9 golden set: OK (protocol, schema, template fixture valid; agreement metric verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
