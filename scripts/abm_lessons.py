#!/usr/bin/env python3
"""Lessons as code (D-99): generate config entries and stub tests from the archive, and check.

    python scripts/abm_lessons.py generate <path/to/lessons.md>   # prints YAML entries and tests
    python scripts/abm_lessons.py check [--run-tests]   # exit 0 ok, 1 failed, 3 incomplete

``check`` fails if any lesson has no guard (or is ``process`` only) or no test; ``--run-tests``
also runs those tests. It never reports ok while the archive is absent (exit 3, INCOMPLETE).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agents.config import load_skill_config  # noqa: E402
from agents.lessons import (  # noqa: E402
    generate_entries,
    lessons_check,
    parse_lessons_md,
    stub_tests,
)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "generate":
        entries = generate_entries(parse_lessons_md(Path(argv[1]).read_text()))
        print(yaml.safe_dump({"lessons": entries}, sort_keys=False, allow_unicode=True))
        print("# ---- tests/agents/lessons/test_lessons.py ----")
        print(stub_tests(entries))
        return 0
    if argv and argv[0] == "check":
        cfg = load_skill_config()
        report = lessons_check(cfg.lessons, cfg.lessons_archive_status, ROOT / "tests" / "agents",
                               ROOT / "src" / "agents")
        for lesson_id, problem in report.problems:
            print(f"{lesson_id}: {problem}")
        if report.status == "failed":
            print(f"FAILED: {len(report.problems)} problem(s) in {report.checked} lesson(s)")
            return 1
        if "--run-tests" in argv and cfg.lessons:
            names = " or ".join(str(e["test"]) for e in cfg.lessons)
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/agents", "-k",
                                     names], cwd=ROOT, check=False)
            if result.returncode:
                print("FAILED: a lesson test does not pass")
                return 1
        if report.status == "incomplete":
            print(f"INCOMPLETE: {report.checked} lesson(s); skill archive is "
                  f"{cfg.lessons_archive_status}, lessons.md not loaded")
            return 3
        print(f"OK: {report.checked} lesson(s), every one guarded and tested")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
