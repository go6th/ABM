"""People source comparison (P0-3 protocol, docs/people-source-comparison.md).

Compares two people lists for the same account (each a JSON list of {profile_url, title}) by
profile URL first and normalised name second (the merge order in config/people_scope.yaml): how
many each source found, the overlap, what only one found, and how many are in scope (seniority
kept, excluded functions removed). Every figure names its base.

    python -m evals.people_compare a.json b.json
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR


def _key(person: dict[str, Any]) -> str:
    url = str(person.get("profile_url") or "").rstrip("/").lower()
    return url or "name:" + re.sub(r"\W+", " ", str(person.get("name", "")).lower()).strip()


def in_scope(title: str, scope: dict[str, Any]) -> bool:
    """Seniority kept and no excluded function in the title (people_scope.yaml)."""
    t = title.lower()
    if any(term in t for term in scope["excluded_title_terms"]):
        return False
    levels = [lv.lower() for lv in scope["levels_in_scope"]]
    aliases = {"c-level": ("chief", "cto", "cio", "ceo", "cfo", "coo")}
    return any(lv in t or any(a in t for a in aliases.get(lv, ())) for lv in levels)


@dataclass(frozen=True)
class Comparison:
    """Counts with their bases."""

    a_found: int
    b_found: int
    overlap: int
    only_a: int
    only_b: int
    a_in_scope: int
    b_in_scope: int


def compare(a: Sequence[dict[str, Any]], b: Sequence[dict[str, Any]]) -> Comparison:
    """Compare two lists for one account."""
    scope = yaml.safe_load((CONFIG_DIR / "people_scope.yaml").read_text())
    ka = {_key(p): p for p in a}
    kb = {_key(p): p for p in b}
    both = ka.keys() & kb.keys()
    return Comparison(
        len(ka),
        len(kb),
        len(both),
        len(ka.keys() - both),
        len(kb.keys() - both),
        sum(in_scope(str(p.get("title", "")), scope) for p in ka.values()),
        sum(in_scope(str(p.get("title", "")), scope) for p in kb.values()),
    )


def main(argv: list[str]) -> int:
    """Print the comparison of two list files."""
    a, b = (json.loads(Path(x).read_text()) for x in argv[:2])
    c = compare(a, b)
    print(f"source A found {c.a_found} ({c.a_in_scope} of {c.a_found} in scope)")
    print(f"source B found {c.b_found} ({c.b_in_scope} of {c.b_found} in scope)")
    print(f"overlap {c.overlap}; only A {c.only_a}; only B {c.only_b}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
