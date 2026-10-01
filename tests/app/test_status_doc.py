"""docs/PHASE0_STATUS.md stays honest: valid statuses, and cited evidence exists."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATUS_DOC = REPO / "docs" / "PHASE0_STATUS.md"
ALLOWED = {"Done", "Needs human", "Blocked"}


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_every_status_is_allowed() -> None:
    rows = _table_rows(STATUS_DOC.read_text(encoding="utf-8"))
    statuses = [r[1] for r in rows if len(r) >= 3 and r[1] != "Status"]
    assert len(statuses) >= 25
    assert set(statuses) <= ALLOWED, set(statuses) - ALLOWED


def test_every_exit_criterion_from_the_pdf_is_listed() -> None:
    text = STATUS_DOC.read_text(encoding="utf-8")
    for criterion in (
        "Decision log signed off by the tech lead, the analyst owner and the product owner",
        "CI green on every commit",
        "A deploy to staging in one command",
        "At least three golden accounts stored and loadable by tests",
        "Phase 1 backlog estimated in person-weeks and agreed",
    ):
        assert criterion in text


def test_needs_human_rows_say_what_to_do() -> None:
    for row in _table_rows(STATUS_DOC.read_text(encoding="utf-8")):
        if len(row) == 4 and row[1] == "Needs human":
            assert len(row[3]) > 15, row[0]


def test_cited_paths_exist() -> None:
    text = STATUS_DOC.read_text(encoding="utf-8")
    cited = re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+(?:::[A-Za-z0-9_]+)?)`", text)
    assert len(cited) > 20
    for ref in cited:
        path, _, test_name = ref.partition("::")
        target = REPO / path
        assert target.exists(), ref
        if test_name:
            assert f"def {test_name}(" in target.read_text(encoding="utf-8"), ref
