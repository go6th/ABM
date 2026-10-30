"""Mutation testing gate: the per-module score of the last ``mutmut run`` and the 90 percent bar.

Reads the ``*.meta`` files mutmut leaves under ``mutants/`` (no mutmut import, no network). For
each mutated module it counts the mutants killed (a timeout is a kill: the mutant hung the
tests), survived, never tested, and the survivors listed in the equivalents file
(``docs/testing/mutation_equivalents.txt``) as equivalent: a change no test can observe, each
with its reason. The score of a module is
killed / (all mutants - listed equivalents). Exit 1 when a module is under the threshold, when a
mutant had no test, or was never checked. A listed id that did not survive is reported as stale.

    uv run --frozen --with "mutmut>=3.8,<4" mutmut run --max-children 3
    uv run --frozen python scripts/mutation_report.py [--threshold 90] [--markdown]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KILLED = {1, 3, 24, -24, 36, 152, 255}  # the exit codes mutmut files under killed or timeout
SURVIVED = {0}
NO_TESTS = {5, 33}


def status_of(exit_code: int | None) -> str:
    """The verdict word of one mutmut exit code."""
    if exit_code is None:
        return "unchecked"
    if exit_code in KILLED:
        return "killed"
    if exit_code in SURVIVED:
        return "survived"
    if exit_code in NO_TESTS:
        return "no tests"
    return "other"


def read_equivalents(path: Path) -> dict[str, str]:
    """``{mutant id: reason}`` from the equivalents file (``id :: reason`` per line)."""
    found: dict[str, str] = {}
    if not path.exists():
        return found
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and " :: " in line:
            mutant, _, reason = line.partition(" :: ")
            found[mutant.strip()] = reason.strip()
    return found


def read_mutants(mutants: Path) -> dict[str, dict[str, str]]:
    """``{module: {mutant id: verdict}}`` from every ``*.meta`` under ``mutants/``."""
    out: dict[str, dict[str, str]] = {}
    for meta in sorted(mutants.rglob("*.py.meta")):
        codes = json.loads(meta.read_text()).get("exit_code_by_key", {})
        if not codes:
            continue
        relative = meta.relative_to(mutants).with_suffix("").with_suffix("")
        module = ".".join(relative.parts[1:] if relative.parts[0] == "src" else relative.parts)
        out[module] = {key: status_of(code) for key, code in codes.items()}
    return out


def score_of(verdicts: dict[str, str], equivalents: set[str]) -> tuple[Counter[str], int, float]:
    """``(counts, equivalent survivors, percent)`` of one module."""
    counts = Counter(verdicts.values())
    equal = sum(1 for key, word in verdicts.items() if word == "survived" and key in equivalents)
    base = len(verdicts) - equal
    return counts, equal, (100.0 * counts["killed"] / base) if base else 100.0


def main() -> int:
    """Print the table; return the exit code (1: under the bar, untested or unchecked)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mutants", type=Path, default=ROOT / "mutants")
    parser.add_argument(
        "--equivalents", type=Path, default=ROOT / "docs/testing/mutation_equivalents.txt"
    )
    parser.add_argument("--threshold", type=float, default=90.0)
    parser.add_argument("--markdown", action="store_true", help="print a Markdown table")
    args = parser.parse_args()

    modules = read_mutants(args.mutants)
    if not modules:
        print(f"no mutation results under {args.mutants}: run mutmut first", file=sys.stderr)
        return 1
    listed = read_equivalents(args.equivalents)
    equivalents = set(listed)
    head = (
        "module",
        "mutants",
        "killed",
        "survived",
        "equivalent",
        "no tests",
        "unchecked",
        "score",
    )
    rows: list[tuple[str, ...]] = []
    failed: list[str] = []
    totals: Counter[str] = Counter()
    total_equal = 0
    seen: set[str] = set()
    for module, verdicts in sorted(modules.items()):
        counts, equal, percent = score_of(verdicts, equivalents)
        seen |= {key for key, word in verdicts.items() if word == "survived"}
        totals.update(counts)
        total_equal += equal
        rows.append(
            (
                module,
                str(len(verdicts)),
                str(counts["killed"]),
                str(counts["survived"] - equal),
                str(equal),
                str(counts["no tests"]),
                str(counts["unchecked"] + counts["other"]),
                f"{percent:.1f}%",
            )
        )
        if percent < args.threshold or counts["no tests"] or counts["unchecked"] or counts["other"]:
            failed.append(module)
    count = sum(totals.values())
    base = count - total_equal
    overall = 100.0 * totals["killed"] / base if base else 100.0
    rows.append(
        (
            "all",
            str(count),
            str(totals["killed"]),
            str(totals["survived"] - total_equal),
            str(total_equal),
            str(totals["no tests"]),
            str(totals["unchecked"] + totals["other"]),
            f"{overall:.1f}%",
        )
    )
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]

    def show(cells: tuple[str, ...]) -> None:
        padded = [
            c.ljust(w) if i == 0 else c.rjust(w)
            for i, (c, w) in enumerate(zip(cells, widths, strict=True))
        ]
        print(("| " + " | ".join(padded) + " |") if args.markdown else "  ".join(padded))

    show(head)
    if args.markdown:
        print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        show(row)
    stale = sorted(key for key in equivalents if key not in seen)
    if stale:
        print(
            f"\n{len(stale)} listed equivalent(s) no longer survive or no longer exist:",
            file=sys.stderr,
        )
        for key in stale[:20]:
            print(f"  {key}", file=sys.stderr)
    if failed:
        print(
            f"\nunder {args.threshold:.0f}% or not fully tested: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    print(f"\nevery module is at or above {args.threshold:.0f}% of its non-equivalent mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
