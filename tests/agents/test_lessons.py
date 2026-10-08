"""Lessons as code (D-99, G12): generator, check, and the absent-archive state."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agents.config import load_skill_config
from agents.lessons import (
    entry_problem,
    generate_entries,
    lessons_check,
    parse_lessons_md,
    require_guard,
    stub_tests,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lessons_synthetic.md"
PROMPTS = Path("src/agents")
TESTS = Path("tests/agents")


def entries() -> list[dict]:  # type: ignore[type-arg]
    return generate_entries(parse_lessons_md(FIXTURE.read_text()))


def test_parser_reads_dated_bullets_with_line_numbers() -> None:
    parsed = parse_lessons_md(FIXTURE.read_text())
    assert [(e.id, e.date, e.line) for e in parsed] == [
        ("L-20260920-1", "2026-09-20", 4),
        ("L-20260920-2", "2026-09-20", 5),
        ("L-20260929-1", "2026-09-29", 10),
    ]


def test_generated_entries_start_as_process_and_fail_the_check() -> None:
    gen = entries()
    assert all(e["enforcement"] == "process" and e["source"].startswith("lessons.md:") for e in gen)
    report = lessons_check(gen, "present", TESTS, PROMPTS)
    assert report.status == "failed"
    assert {p for _, p in report.problems} == {"process_only", "no_test"}
    assert "def test_lesson_l_20260920_1() -> None:" in stub_tests(gen)


def guarded(**change: object) -> dict:  # type: ignore[type-arg]
    e = entries()[0]
    e.update(
        {
            "enforcement": "code",
            "guard": "G1",
            "test": "test_g1_every_row_needs_a_terminal_status_with_proof",
        },
        **change,
    )
    return e


def test_lessons_check_passes_when_every_lesson_is_guarded_and_tested() -> None:
    report = lessons_check([guarded()], "present", TESTS, PROMPTS)
    assert (report.status, report.checked) == ("ok", 1)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"guard": None}, "no_guard"),
        ({"guard": "G99"}, "no_guard"),  # deleting or renaming a guard fails the suite
        ({"test": "test_that_does_not_exist"}, "no_test"),
        ({"enforcement": "vibes"}, "unknown_enforcement"),
        ({"source": "memory"}, "no_dated_source_line"),
        ({"id": "lesson-1"}, "bad_id"),
        ({"rule": ""}, "no_rule_text"),
        ({"enforcement": "prompt", "guard": "distill"}, "prompt_not_tagged"),
    ],
)
def test_lessons_check_fails_without_guard_or_test(change, problem) -> None:  # type: ignore[no-untyped-def]
    report = lessons_check([guarded(**change)], "present", TESTS, PROMPTS)
    assert (
        report.status == "failed"
        and (guarded(**change)["id"], problem) in report.problems
        or ("lesson-1", problem) in report.problems
    )


def test_prompt_enforced_lesson_needs_the_tag(tmp_path: Path) -> None:
    (tmp_path / "distill").mkdir()
    (tmp_path / "distill" / "prompt.md").write_text("x [L-20260920-1]")
    e = guarded(enforcement="prompt", guard="distill")
    assert entry_problem(e, tmp_path) is None


def test_duplicate_lesson_ids_fail() -> None:
    report = lessons_check([guarded(), guarded()], "present", TESTS, PROMPTS)
    assert ("L-20260920-1", "duplicate_id") in report.problems


def test_lessons_check_is_incomplete_while_the_archive_is_absent() -> None:
    cfg = load_skill_config()
    report = lessons_check(cfg.lessons, cfg.lessons_archive_status, TESTS, PROMPTS)
    assert report.status == "incomplete" and cfg.lessons == ()
    result = subprocess.run(
        [sys.executable, "scripts/abm_lessons.py", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 3 and "INCOMPLETE" in result.stdout


def test_require_guard() -> None:
    require_guard("L-20260920-1", [guarded()])
    with pytest.raises(AssertionError, match="not in config"):
        require_guard("L-20260920-1", [])
    with pytest.raises(AssertionError, match="process_only"):
        require_guard("L-20260920-1", [guarded(enforcement="process")])
