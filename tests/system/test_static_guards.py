"""Static guards: retry owners (W6), the loop inventory (W8), comments and GUARANTEE tags.

GUARANTEE(test_one_retry_owner_per_failure_class_w6)
GUARANTEE(test_loop_inventory_is_generated_and_complete_w8)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _source() -> dict[str, str]:
    return {str(p.relative_to(ROOT)): p.read_text() for p in SRC.rglob("*.py")}


def test_one_retry_owner_per_failure_class_w6() -> None:
    """Provider 429/5xx: the node RetryPolicy only (built in one place). LLM SDK: max_retries=0.
    HTTP transports: no retries. Version conflicts: the tracker repository loop only. No module
    may add its own retry library or open-ended retry loop."""
    files = _source()
    policies = [f for f, s in files.items() if "RetryPolicy(" in s]
    assert policies == ["src/account_intelligence/connectors/service.py"], policies
    for f, s in files.items():
        for m in re.finditer(r"max_retries\s*=\s*(\w+)", s):
            assert m.group(1) == "0", f"{f}: an SDK client retries on its own"
        assert "tenacity" not in s and "backoff" not in s.split("\n", 1)[0], f
        for m in re.finditer(r"retries\s*=\s*([1-9])", s):
            raise AssertionError(f"{f}: transport retries={m.group(1)}")
    # every while loop and bounded retry carries its cap (W8 shares the check)
    check = subprocess.run([sys.executable, str(ROOT / "scripts/gen_loops.py"), "--check"])
    assert check.returncode == 0


def test_loop_inventory_is_generated_and_complete_w8() -> None:
    text = (ROOT / "docs/architecture/loops.md").read_text()
    for name in (
        "slice loop",
        "planner gap loop",
        "lead verification",
        "gate fix loop",
        "pagination",
        "pacing",
        "tracker version conflict",
        "judge re-ask",
    ):
        assert f"| {name} |" in text, name


def test_every_file_has_its_header_and_every_guarantee_tag_resolves() -> None:
    for script in ("check_comments.py", "check_guarantees.py"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout[-2000:]


def test_interrogate_docstring_coverage_is_complete() -> None:
    exe = Path(sys.executable).with_name("interrogate")
    result = subprocess.run([str(exe), "src"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-1500:]


def test_every_source_package_ships_in_the_wheel() -> None:
    """The images install the wheel; a package left out of it imports only where ``src`` is on
    the path, so the document sandbox child (``python -I``, which ignores the path) and every
    production container would fail to import it."""
    import tomllib

    listed = set(
        tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"][
            "wheel"
        ]["packages"]
    )
    packages = {f"src/{p.name}" for p in SRC.iterdir() if (p / "__init__.py").exists()}
    assert packages - listed == set(), sorted(packages - listed)
