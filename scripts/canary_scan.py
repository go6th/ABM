"""Sink canary scan (O4): search captured sink files (logs, span dumps, error reports, operator
responses, notification bodies) for planted canary strings. Exit 1 on any hit.

    python scripts/canary_scan.py --canary CANARY-7f3a9e FILE...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from obs.canary import scan_texts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Scan; exit 1 on a hit."""
    p = argparse.ArgumentParser()
    p.add_argument("--canary", action="append", required=True)
    p.add_argument("files", nargs="+", type=Path)
    a = p.parse_args(argv)
    hits = scan_texts({str(f): [f.read_text(errors="replace")] for f in a.files}, a.canary)
    for h in hits:
        print(f"canary hit: {h}")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
