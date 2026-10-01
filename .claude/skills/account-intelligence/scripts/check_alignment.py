#!/usr/bin/env python3
"""Cross-check the platform skill's assets against the repository's versioned config.

Two encodings of the same rules exist:
  .claude/skills/account-intelligence-platform/assets/*.yaml   (reference library, ARCH v2 era)
  config/*.yaml                                                (what the code loads and tests pin)
The repository config is canonical. This script fails when the two disagree on something that
would change behaviour, and lists softer differences as notes for a person to settle.

Usage: python check_alignment.py [--repo PATH] [--strict]   (in this skill's scripts/)
Exit 0: no errors (notes may exist). Exit 1: at least one error (or a note with --strict).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CADENCE_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, None: None}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")


def check_checklist(platform: dict[str, Any], repo: dict[str, Any], r: Report) -> None:
    p_rows, c_rows = platform["rows"], repo["rows"]
    if len(p_rows) != len(c_rows):
        r.error(f"checklist: platform has {len(p_rows)} rows, repo has {len(c_rows)}")
        return
    shelf = {k: v.get("days") for k, v in repo["shelf_lives"].items()}
    for i, (p, c) in enumerate(zip(p_rows, c_rows, strict=True), start=1):
        where = f"checklist row {i} ({c['id']} / {p['key']})"
        if p["label"] != c["row"]:
            r.error(f"{where}: label differs: {p['label']!r} vs {c['row']!r}")
        if p["pass"] != c["pass"]:
            r.error(f"{where}: pass differs: {p['pass']} vs {c['pass']}")
        if p["priority"] != _slug(c["priority"]):
            r.error(f"{where}: priority differs: {p['priority']} vs {c['priority']}")
        p_days = CADENCE_DAYS.get(p.get("refresh_cadence"), "unknown")
        c_days = shelf.get(c["shelf_life"])
        if p_days != c_days:
            r.note(
                f"{where}: refresh cadence {p.get('refresh_cadence')} ({p_days} days) vs "
                f"repo shelf life {c['shelf_life']} ({c_days} days); analyst to settle"
            )
    if [_slug(s) for s in repo["statuses"]] != platform["statuses"]:
        r.error(f"checklist statuses differ: {platform['statuses']} vs {repo['statuses']}")


def check_grading(platform: dict[str, Any], repo: dict[str, Any], r: Report) -> None:
    p_grades = [g for g in platform["product_grades"] if g != "internal"]
    c_grades = [_slug(g["name"]) for g in repo["grades"]]
    if p_grades != c_grades:
        r.error(f"grades differ: {p_grades} vs {c_grades}")
    if "internal" in platform["product_grades"]:
        r.note("grade 'internal' is Phase B only (private context); not in config/grading.yaml yet")
    lead_p = platform["product_grades"]["lead"]["stays_lead_only_if"]
    lead_c = repo["rules"]["lead_verification"]
    if (lead_p["min_distinct_queries"], lead_p["min_distinct_tool_categories"]) != (
        lead_c["min_distinct_queries"],
        lead_c["min_distinct_tools"],
    ):
        r.error(f"Lead verification rule differs: {lead_p} vs {lead_c}")
    if platform["absence_of_evidence_grade"] != _slug(repo["rules"]["absence_of_evidence_grade"]):
        r.error("absence-of-evidence grade differs")
    stateable = [g for g, v in platform["product_grades"].items() if v.get("may_state_to_prospect")]
    if stateable != ["confirmed"]:
        r.error(f"platform lets {stateable} be stated to a prospect; only confirmed may be")
    if platform.get("proposed_mapping", {}).get("status") == "proposal":
        r.note("engine grades A to D and their mapping (decision D-01) are not in config yet")


def check_routing(
    platform: dict[str, Any], repo: dict[str, Any], denylist: list[str], r: Report
) -> None:
    p_names = {v["name"].casefold() for v in platform["providers"].values()}
    c_names = {b["display_name"].casefold() for k, b in repo["brokers"].items() if k != "builtin"}
    if p_names != c_names:
        r.error(f"broker sets differ: {sorted(p_names)} vs {sorted(c_names)}")
    patterns = [re.compile(p, re.I) for p in denylist]
    for name in sorted(c_names):
        probes = (name, f"{name} search", f"source: {name}")
        if not any(p.search(probe) for p in patterns for probe in probes):
            r.error(f"client output denylist has no pattern catching broker {name!r}")
    p_needs = {route["need"] for route in platform["routes"]}
    if "client_history" in p_needs:
        r.note(
            "route client_history (tenant connectors) is Phase 3; config/routing.yaml has none yet"
        )
    if len(platform["routes"]) - ("client_history" in p_needs) != len(repo["routes"]):
        r.note(
            f"route count differs: platform {len(platform['routes'])}, repo {len(repo['routes'])}"
        )


def run(platform_assets: Path, repo_config: Path) -> Report:
    r = Report()
    check_checklist(
        _load(platform_assets / "checklist.yaml"), _load(repo_config / "checklist.yaml"), r
    )
    check_grading(_load(platform_assets / "grading.yaml"), _load(repo_config / "grading.yaml"), r)
    denylist = [
        line.strip()
        for line in (platform_assets / "client_output_denylist.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    check_routing(
        _load(platform_assets / "routing.yaml"), _load(repo_config / "routing.yaml"), denylist, r
    )
    return r


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve()
    default_repo = here.parents[4]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=default_repo)
    parser.add_argument("--strict", action="store_true", help="treat notes as errors")
    args = parser.parse_args(argv)
    platform = args.repo / ".claude" / "skills" / "account-intelligence-platform" / "assets"
    report = run(platform, args.repo / "config")
    for msg in report.errors:
        print(f"ERROR {msg}")
    for msg in report.notes:
        print(f"NOTE  {msg}")
    failed = bool(report.errors) or (args.strict and bool(report.notes))
    print(f"{'FAILED' if failed else 'OK'}: {len(report.errors)} errors, {len(report.notes)} notes")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
