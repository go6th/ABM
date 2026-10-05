"""People source comparison (P0-3 protocol, docs/people-source-comparison.md).

Compares two people lists for the same account (each a JSON list of {profile_url, title}) by
profile URL first and normalised name second (the merge order in config/people_scope.yaml): how
many each source found, the overlap, what only one found, and how many are in scope (seniority
kept, excluded functions removed). Every figure names its base.

    python -m evals.people_compare a.json b.json
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from people.config import load_people_config
from people.exclusion import check_exclusion
from people.level import classify_level
from people.names import normalise_name
from people.urls import canonical_profile_url


def _key(person: dict[str, Any]) -> str:
    """The pipeline's merge keys: canonical profile URL, else the normalised name."""
    url = canonical_profile_url(
        str(person.get("profile_url") or ""), load_people_config().profile_url
    )
    return url or "name:" + normalise_name(str(person.get("name", "")))


def in_scope(title: str) -> bool:
    """The people pipeline's own title rules (one implementation, ``people.exclusion`` and
    ``people.level``): no exclusion or review cue, and a level in scope."""
    cfg = load_people_config()
    if check_exclusion(title, cfg.exclusion_rules, cfg.review_rules).outcome != "include":
        return False
    level = classify_level(title, cfg.level_rules, cfg.level_order)
    return level is not None and level.level in cfg.levels_in_scope


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
    ka = {_key(p): p for p in a}
    kb = {_key(p): p for p in b}
    both = ka.keys() & kb.keys()
    return Comparison(
        len(ka),
        len(kb),
        len(both),
        len(ka.keys() - both),
        len(kb.keys() - both),
        sum(in_scope(str(p.get("title", ""))) for p in ka.values()),
        sum(in_scope(str(p.get("title", ""))) for p in kb.values()),
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
