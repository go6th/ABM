"""The repair report and the exclusions log."""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

import pytest

from exports.repair import core as K
from exports.repair import report as R
from exports.repair.checks import CheckResult
from exports.repair.core import Exclusion, RepairedTable, Sheet, WorkspaceProfile

RUN = date(2026, 10, 30)


def profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name="Quill & Anvil Foods",
        account_short="Quill",
        domain="quill.example",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
    )


def read_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


def coverage_table() -> RepairedTable:
    cols = K.COVERAGE_COLUMNS
    rows = [
        {
            "#": "1",
            "Research item": "Job postings",
            "Priority": "A",
            "Status": "Done",
            "Reason": "Not applicable",
            "Evidence (row counts / source codes)": "12 rows",
            "Last run": "2026-10-30",
        },
        {
            "#": "2",
            "Research item": "People",
            "Priority": "A",
            "Status": "Not done",
            "Reason": "people data consent needed",
            "Evidence (row counts / source codes)": "0",
            "Last run": "Unknown",
        },
        {
            "#": "3",
            "Research item": "Mail",
            "Priority": "B",
            "Status": "Not done",
            "Reason": "mail and drive not connected",
            "Evidence (row counts / source codes)": "0",
            "Last run": "Unknown",
        },
        {
            "#": "4",
            "Research item": "Web",
            "Priority": "B",
            "Status": "Partial",
            "Reason": "execution cap reached after 40 searches",
            "Evidence (row counts / source codes)": "9",
            "Last run": "2026-10-29",
        },
        {
            "#": "5",
            "Research item": "Odd",
            "Priority": "C",
            "Status": "Partial",
            "Reason": "supplier did not answer",
            "Evidence (row counts / source codes)": "1",
            "Last run": "2026-10-29",
        },
    ]
    return RepairedTable("coverage", "Coverage", [Sheet("Coverage", cols, rows)])


def jobs_table() -> RepairedTable:
    cols = ("Posting ID", "Title")
    rows = [{"Posting ID": "1", "Title": "Engineer"}, {"Posting ID": "2", "Title": ""}]
    return RepairedTable(
        key="jobs",
        title="Job postings",
        sheets=[Sheet("Job postings", cols, rows)],
        exclusions=[
            Exclusion("Job postings", "J-7", "duplicate posting", "merged into 1"),
            Exclusion("Job postings", "J-8", "domain collision", "other company"),
            Exclusion("Job postings", "J-9", "domain collision", "other company"),
            Exclusion("Job postings", "J-10", "title is a platform name", ""),
        ],
        stats={"account_postings": 2, "per_platform": {"Alpha": {"postings": 5, "open": 2}}},
        notes=["Live board not reached: the careers site blocked the request."],
    )


def people_table() -> RepairedTable:
    return RepairedTable(
        key="people",
        title="People",
        sheets=[Sheet("People", ("Name",), [{"Name": "Ada Byrne"}])],
        stats={"total": 736, "meets_seniority": 424},
        exclusions=[Exclusion("People", "x", "from news text", "")],
    )


def tables() -> dict[str, RepairedTable]:
    return {t.key: t for t in (jobs_table(), people_table(), coverage_table())}


RESULTS = [
    CheckResult("no_duplicate_keys", True, "3 key sets unique", {"jobs.Job postings": 0}),
    CheckResult("jobs_status", False, "1 problem(s): row 3 status 'Pending'", {"problems": 1}),
]


def report(**kw: Any) -> str:
    args: dict[str, Any] = {
        "before": {
            "jobs": R.TableBefore(rows=9, empty_cells=7, duplicate_keys=3),
            "people": R.TableBefore(rows=800, empty_cells=0, duplicate_keys=0),
        },
        "tables": tables(),
        "results": RESULTS,
        "changes": [
            ("src/jobs.py", "merge_postings", "merge on posting id", "one row per posting")
        ],
        "pending": ["Search budget: the owner has not approved the extra searches."],
    }
    args.update(kw)
    return R.build_report(
        profile(),
        args["before"],
        args["tables"],
        args["results"],
        args["changes"],
        args["pending"],
        **{k: v for k, v in kw.items() if k in ("baseline", "baseline_notes")},
    )


# ---- exclusions.csv ---------------------------------------------------------------------------


def test_exclusions_csv_format_and_sorting() -> None:
    data = R.exclusions_csv(tables())
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert text.split("\r\n")[0] == '"Table","Key","Reason","Detail"'
    rows = read_csv(data)
    assert rows[1:] == [
        ["Job postings", "J-10", "title is a platform name", ""],
        ["Job postings", "J-7", "duplicate posting", "merged into 1"],
        ["Job postings", "J-8", "domain collision", "other company"],
        ["Job postings", "J-9", "domain collision", "other company"],
        ["People", "x", "from news text", ""],
    ]


def test_exclusions_csv_header_only_when_nothing_was_excluded() -> None:
    clean = RepairedTable("coverage", "Coverage", [Sheet("Coverage", ("#",), [])])
    assert read_csv(R.exclusions_csv({"coverage": clean})) == [["Table", "Key", "Reason", "Detail"]]
    assert read_csv(R.exclusions_csv([])) == [["Table", "Key", "Reason", "Detail"]]


def test_exclusions_csv_adds_extra_rows_and_escapes_formula_like_keys() -> None:
    extra = [Exclusion("A table", "=cmd", "cell withheld for review", "column X")]
    rows = read_csv(R.exclusions_csv(tables(), extra))
    assert ["A table", "'=cmd", "cell withheld for review", "column X"] in rows
    assert rows[1][0] == "A table"  # sorted by table first


def test_csv_document_is_quoted_with_bom_and_crlf() -> None:
    data = R.csv_document(["A"], [["x"], ["+y"]])
    assert data == b'\xef\xbb\xbf"A"\r\n"x"\r\n"\'+y"\r\n'


# ---- the report -------------------------------------------------------------------------------


def test_report_has_every_section_and_the_summary_numbers() -> None:
    text = report()
    for heading in (
        "# Repair report: Quill & Anvil Foods",
        "## 1. Summary",
        "## 2. Before and after, per table",
        "## 3. Acceptance checks",
        "## 4. Pipeline code changes",
        "## 5. Still Not done or Partial",
    ):
        assert heading in text
    assert "## 6." not in text  # no baseline passed
    assert "Run date: 2026-10-30" in text
    assert "Window: 2025-10-30 to 2026-10-30" in text
    assert "1 passed, 1 failed (2 in all)" in text
    assert "CHECKS FAILED" in text
    assert text.endswith("\n") and "\r" not in text


def test_summary_says_all_passed_when_nothing_failed() -> None:
    text = report(results=[CheckResult("a", True, "ok", {})])
    assert "every acceptance check passed" in text and "CHECKS FAILED" not in text


def test_before_after_table_counts() -> None:
    text = report()
    line = next(ln for ln in text.splitlines() if ln.startswith("| Job postings |"))
    cells = [c.strip() for c in line.strip("|").split("|")]
    # table, rows in, rows out, dup before, dup removed, dropped, empty before, empty after, filled
    assert cells == ["Job postings", "9", "2", "3", "1", "3", "7", "1", "6"]
    people = next(ln for ln in text.splitlines() if ln.startswith("| People |"))
    assert "| 800 | 1 |" in people
    assert "- Job postings: domain collision: 2; title is a platform name: 1" in text
    assert "- People: from news text: 1" in text


def test_table_without_a_before_record_is_marked() -> None:
    text = report(before={})
    assert "not recorded" in next(
        ln for ln in text.splitlines() if ln.startswith("| Job postings |")
    )


def test_acceptance_results_show_pass_fail_and_numbers() -> None:
    text = report()
    assert "| no_duplicate_keys | PASS | 3 key sets unique | jobs.Job postings=0 |" in text
    assert "| jobs_status | FAIL |" in text and "problems=1" in text


def test_pipes_and_line_breaks_in_cells_cannot_break_the_table() -> None:
    res = [CheckResult("x", True, "a | b\nc", {})]
    text = report(results=res)
    assert "| x | PASS | a / b c |  |" in text


def test_pipeline_changes_listed_or_stated_absent() -> None:
    assert (
        "| src/jobs.py | merge_postings | merge on posting id | one row per posting |" in report()
    )
    assert "No pipeline code changed in this run." in report(changes=[])


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("people data consent needed", "people data consent"),
        ("mail and drive not connected", "Connect the mail and drive"),
        ("web searches were not run", "web searches"),
        ("execution cap reached", "execution cap"),
        ("feature switched off", "Enable the feature"),
        ("supplier did not answer", "no approval keyword"),
        ("the capital of nowhere", "no approval keyword"),
    ],
)
def test_unblock_actions_come_from_reason_words(reason: str, expected: str) -> None:
    assert expected.casefold() in R.unblock_for(reason).casefold()


def test_not_done_section_lists_non_done_coverage_notes_and_pending() -> None:
    text = report()
    section = text.split("## 5.")[1]
    assert (
        "| 2 | People | Not done | people data consent needed | Approve people data consent"
        in section
    )
    assert (
        "| 3 | Mail | Not done | mail and drive not connected | Connect the mail and drive"
        in section
    )
    assert (
        "| 4 | Web | Partial | execution cap reached after 40 searches | Raise or lift" in section
    )
    assert "| 1 | Job postings" not in section  # Done rows are not listed
    assert "Job postings: Live board not reached" in section
    assert "Search budget: the owner has not approved the extra searches." in section


def test_nothing_pending_when_coverage_is_done_and_there_are_no_notes() -> None:
    t = coverage_table()
    for r in t.main.rows:
        r["Status"] = "Done"
    text = report(tables={"coverage": t}, before={}, pending=[])
    assert "Nothing is marked Not done or Partial." in text


def test_regression_block_matches_differs_and_not_compared() -> None:
    baseline = {
        "jobs.account_postings": 2,
        "people.total": 700,
        "jobs.per_platform.Alpha.postings": 5,
        "jobs.missing_number": 1,
    }
    text = report(baseline=baseline, baseline_notes={"people.total": "the window moved by 11 days"})
    section = text.split("## 6.")[1]
    assert "| jobs.account_postings | 2 | 2 | same |" in section
    assert "| jobs.per_platform.Alpha.postings | 5 | 5 | same |" in section
    assert "| people.total | 700 | 736 | +36: the window moved by 11 days |" in section
    assert "| jobs.missing_number | 1 | not compared | not compared" in section


def test_regression_difference_without_a_note_gets_a_generic_explanation() -> None:
    text = report(baseline={"people.total": 700})
    assert "different date or scope" in text.split("## 6.")[1]
    assert "2025-10-30 to 2026-10-30" in text.split("## 6.")[1]


def test_table_numbers_flatten_statistics_and_count_rows() -> None:
    nums = R.table_numbers(tables())
    assert nums["jobs.rows"] == 2 and nums["jobs.account_postings"] == 2
    assert nums["jobs.per_platform.Alpha.open"] == 2
    assert nums["people.meets_seniority"] == 424


def test_report_is_deterministic() -> None:
    assert report() == report()
