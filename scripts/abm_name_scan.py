#!/usr/bin/env python3
"""Fail if a forbidden name (held only as a digest in config/skill/name_digests.yaml) appears in
repository text files (work item 10). The exact user-supplied operating guide is an input reference,
not an output, and is the only source-reference exemption. Prints paths and line numbers only.

    python scripts/abm_name_scan.py      # exit 0 clean, 1 found
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.naming import digest_hits  # noqa: E402

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".zip",
    ".gz",
    ".ico",
    ".woff",
    ".woff2",
    ".lock",
}

# The exact user-supplied operating guide is retained as an input reference. Its examples include
# names that the output guard correctly prohibits in generated deliverables; it is never an output.
SOURCE_REFERENCE_FILES = {"config/skill/archive/references/session-environment.md"}


def files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return [
        ROOT / p
        for p in out
        if Path(p).suffix.lower() not in SKIP_SUFFIXES and p not in SOURCE_REFERENCE_FILES
    ]


def scan() -> list[tuple[str, int]]:
    found = []
    for path in files():
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if digest_hits(line):
                found.append((str(path.relative_to(ROOT)), number))
    return found


if __name__ == "__main__":
    hits = scan()
    for path, line in hits:
        print(f"{path}:{line}: forbidden name")
    print("OK: no forbidden name" if not hits else f"FAILED: {len(hits)} hit(s)")
    raise SystemExit(1 if hits else 0)
