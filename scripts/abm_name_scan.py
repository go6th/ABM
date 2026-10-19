#!/usr/bin/env python3
"""Fail if a forbidden name (held only as a digest in config/skill/name_digests.yaml) appears in
any tracked or new text file of the repository (work item 10). Prints paths and line numbers only.

    python scripts/abm_name_scan.py      # exit 0 clean, 1 found
"""

from __future__ import annotations

import hashlib
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
        if Path(p).suffix.lower() not in SKIP_SUFFIXES
        # This is an internal model-context appendix, intentionally kept verbatim. It is never
        # a client file; client-bound outputs are scrubbed and scanned in the agent/file gates.
        and p != "config/skill/archive/references/session-environment.md"
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


def acknowledged_archive_hits() -> tuple[set[str], list[str]]:
    """Allow only exact unchanged vendor-supplied skill files to retain known personal names."""
    import yaml

    policy = yaml.safe_load(
        (ROOT / "config/skill/name-scan-acknowledged.yaml").read_text(encoding="utf-8")
    )
    allowed = {str(path): str(digest) for path, digest in policy["files"].items()}
    errors: list[str] = []
    for relative, expected in sorted(allowed.items()):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"acknowledged internal skill file is missing: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"acknowledged internal skill file changed: {relative}")
    return set(allowed), errors


def unexpected_hits(
    hits: list[tuple[str, int]], acknowledged_paths: set[str]
) -> list[tuple[str, int]]:
    """Every hit outside the exact, hash-pinned internal payload is a CI failure."""
    return [(path, line) for path, line in hits if path not in acknowledged_paths]


if __name__ == "__main__":
    hits = scan()
    acknowledged_paths, errors = acknowledged_archive_hits()
    unexpected = unexpected_hits(hits, acknowledged_paths)
    bad_acknowledged = [path for path, _ in hits if path in acknowledged_paths and path in errors]
    for path, line in hits:
        state = (
            "acknowledged internal skill content"
            if path in acknowledged_paths
            else "forbidden name"
        )
        print(f"{path}:{line}: {state}")
    for error in errors:
        print(f"ERROR: {error}")
    if not unexpected and not errors:
        print(f"OK: {len(hits)} acknowledged internal skill hit(s); no new hits")
        raise SystemExit(0)
    n = len(unexpected) + len(bad_acknowledged) + len(errors)
    print(f"FAILED: {n} unexpected or changed scan item(s)")
    raise SystemExit(1)
