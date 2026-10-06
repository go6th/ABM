"""Runbook lint (O14): every runbook an alert names exists and has the required sections, an
owner line, and first checks that point at query files which exist. Exit 1 on any gap. The
queries themselves are executed against a seeded database by tests/obs."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("## Symptom", "## Impact", "## First checks", "## Mitigation", "## Escalation",
            "## Verification", "## Follow-up")
FORBIDDEN = ("bypass rls", "set role postgres", "disable row level")


def problems(root: Path = ROOT) -> list[str]:
    """Every gap."""
    out: list[str] = []
    rb = root / "docs/runbooks"
    alerts = (root / "config/telemetry/alerts.yaml").read_text()
    for name in sorted(set(re.findall(r"runbook: ([\w.-]+\.md)", alerts))):
        path = rb / name
        if not path.exists():
            out.append(f"missing runbook {name}")
            continue
        text = path.read_text()
        for section in SECTIONS:
            if section not in text:
                out.append(f"{name}: no '{section}'")
        if "Owner:" not in text:
            out.append(f"{name}: no owner line")
        for q in re.findall(r"`(queries/[\w.-]+\.sql)`", text):
            if not (rb / q).exists():
                out.append(f"{name}: query {q} missing")
        lowered = text.lower()
        for bad in FORBIDDEN:
            if bad in lowered and f"never {bad}" not in lowered:
                out.append(f"{name}: asks to {bad}")
    return out


def main() -> int:
    """Print gaps; exit 1 when any."""
    gaps = problems()
    for g in gaps:
        print(g)
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
