"""Section 10: every ``GUARANTEE(<test id>)`` tag names a test that exists. Exit 1 otherwise.

Tags sit in module or function docstrings next to the code or test that makes the guarantee, so
a reader can jump from a promise to its proof; this check keeps the tags from pointing at tests
that were renamed or removed. It also requires the integration guards W1 to W8 to be tagged.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"GUARANTEE\(([a-zA-Z_][a-zA-Z0-9_]*)\)")
REQUIRED = (
    "test_each_table_is_written_only_by_its_owner",  # W1
    "test_lying_generator_whole_run_persists_no_fabrication",  # W2
    "test_tenant_never_comes_from_a_body_w3",  # W3
    "test_assert_gcp_runtime_refuses_hermetic_adapters_w4",  # W4
    "test_readyz_fails_closed_w5",  # W5
    "test_one_retry_owner_per_failure_class_w6",  # W6
    "test_export_and_count_sentence_are_client_safe",  # W7 (job export), plus the tracker view
    "test_loop_inventory_is_generated_and_complete_w8",  # W8
)


def tests_defined() -> set[str]:
    """Every test function name under tests/."""
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
                "test"
            ):
                names.add(node.name)
    return names


def tags() -> dict[str, list[str]]:
    """Tag to the files that carry it."""
    found: dict[str, list[str]] = {}
    for top in ("src", "tests"):
        for path in (ROOT / top).rglob("*.py"):
            for name in TAG.findall(path.read_text()):
                found.setdefault(name, []).append(str(path.relative_to(ROOT)))
    return found


def main() -> int:
    """Print dangling tags and untagged guards; exit 1 when there are any."""
    defined, found = tests_defined(), tags()
    bad = [
        f"{name} (in {', '.join(files)})" for name, files in found.items() if name not in defined
    ]
    untagged = [name for name in REQUIRED if name not in found or name not in defined]
    for item in bad:
        print(f"GUARANTEE names no test: {item}")
    for item in untagged:
        print(f"guard without a GUARANTEE tag: {item}")
    print(f"{len(found)} guarantee tags, {len(bad)} dangling, {len(untagged)} untagged guards")
    return 1 if bad or untagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
