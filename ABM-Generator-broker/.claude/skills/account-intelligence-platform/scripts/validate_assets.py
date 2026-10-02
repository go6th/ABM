#!/usr/bin/env python3
"""Validate the skill's config assets. Exit 1 on any problem.

Usage: python scripts/validate_assets.py [assets_dir]
Checks: checklist has 33 unique rows (5 in pass 1), valid status/priority values; grading has both
vocabularies; routing references only declared providers; alert rule ids are unique.
"""
import sys
from pathlib import Path

import yaml


def main(root: Path) -> int:
    errs: list[str] = []
    cl = yaml.safe_load((root / "checklist.yaml").read_text())
    rows = cl["rows"]
    keys = [r["key"] for r in rows]
    if len(rows) != 33:
        errs.append(f"checklist: expected 33 rows, found {len(rows)}")
    if len(set(keys)) != len(keys):
        errs.append("checklist: duplicate row keys")
    if sum(1 for r in rows if r["pass"] == 1) != 5:
        errs.append("checklist: expected 5 pass-1 rows")
    for r in rows:
        if r["priority"] not in cl["priorities"]:
            errs.append(f"checklist: bad priority on {r['key']}")
        if r["pass"] not in (1, 2):
            errs.append(f"checklist: bad pass on {r['key']}")

    gr = yaml.safe_load((root / "grading.yaml").read_text())
    for g in ("confirmed", "probable", "hypothesis", "lead"):
        if g not in gr["product_grades"]:
            errs.append(f"grading: missing product grade {g}")
    if not {"A", "B", "C", "D"} <= set(gr["engine"]["grades"]):
        errs.append("grading: engine grades A to D missing")
    if gr["product_grades"]["lead"]["stays_lead_only_if"] != {"min_distinct_queries": 3, "min_distinct_tool_categories": 2}:
        errs.append("grading: Lead verification rule must be 3 queries across 2 tool categories")

    rt = yaml.safe_load((root / "routing.yaml").read_text())
    declared = set(rt["providers"]) | {"web_search_builtin", "fetch_builtin", "tenant_connectors", None}
    for route in rt["routes"]:
        for k in ("first", "fallback"):
            if route[k] not in declared:
                errs.append(f"routing: {route['need']} references undeclared {route[k]}")

    al = yaml.safe_load((root / "alert_rules.yaml").read_text())
    ids = [r["id"] for r in al["rules"]]
    if len(set(ids)) != len(ids):
        errs.append("alert_rules: duplicate ids")

    if errs:
        print("\n".join("FAIL " + e for e in errs))
        return 1
    print(f"OK: {len(rows)} coverage rows, {len(gr['product_grades'])} product grades, "
          f"{len(rt['routes'])} routes, {len(al['rules'])} alert rules")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets"))
