"""Section 11 conformance report: writes docs/crosscutting/CONFORMANCE.md and prints it.

Usage: ``ADMIN_DSN=... python scripts/check_conformance.py [--out PATH] [--strict]``
``--strict`` exits 1 when any requirement is Missing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

from crosscut import conformance


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", default="docs/crosscutting/CONFORMANCE.md")
    ap.add_argument("--title", default="Section 11 conformance")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    with psycopg.connect(os.environ["ADMIN_DSN"]) as c:
        cat = conformance.load_catalog(c)
    results = conformance.evaluate(conformance.load_spec(), cat)
    text = conformance.render(results, a.title)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)
    print(text)
    return 1 if a.strict and any(r.status == "Missing" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
