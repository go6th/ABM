"""Recalculated workbooks: no errors, formulas kept, Summary values equal the count module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from parity.gate.report import FAIL, PASS, SKIP
from parity.gate.workbook import run_workbook_checks
from tests.parity.gate.conftest import PEOPLE_LANES, office_only, write_workbooks


def test_without_an_engine_the_check_is_skipped(bundle: dict[str, Any], tmp_path: Path) -> None:
    files = write_workbooks(tmp_path, bundle)
    result, summary = run_workbook_checks(files, bundle, None)
    assert result.status == SKIP and summary == {}


@office_only
def test_rendered_workbooks_pass_and_their_values_are_returned(
    bundle: dict[str, Any], tmp_path: Path, office: Any
) -> None:
    files = write_workbooks(tmp_path, bundle)
    result, summary = run_workbook_checks(files, bundle, office)
    assert result.findings == []
    assert result.status == PASS
    jobs, people, tech = (summary[p.name] for p in files)
    assert jobs["jobs.Salesforce.total"] == 3 and jobs["jobs.rows.other"] == 1
    assert people["people.senior"] == 4
    assert people[f"people.lane.{PEOPLE_LANES[0]}.total"] == 2
    assert tech["ranking.SAP.rank"] == "A" and tech["ranking.count.C"] == 1
    assert abs(tech["ranking.Workday.share"] - 0.1) < 1e-9


@office_only
def test_a_row_edited_after_rendering_fails_the_summary_comparison(
    bundle: dict[str, Any], tmp_path: Path, office: Any
) -> None:
    files = write_workbooks(tmp_path, bundle)
    wb = load_workbook(files[0])
    wb["Jobs"]["J2"] = "Elsewhere"  # the first Salesforce row is no longer an account row
    wb.save(files[0])
    result, _ = run_workbook_checks(files[:1], bundle, office)
    assert result.status == FAIL
    assert any(
        "jobs.Salesforce.total: workbook 2, computed 3" in f.message for f in result.findings
    )


@office_only
def test_formula_errors_and_lost_formulas_are_findings(
    bundle: dict[str, Any], tmp_path: Path, office: Any
) -> None:
    files = write_workbooks(tmp_path, bundle)
    wb = load_workbook(files[2])
    ws = wb["Platform Ranking"]
    ws["H40"] = "=1/0"
    wb.save(files[2])
    result, _ = run_workbook_checks(files[2:], bundle, office)
    assert result.status == FAIL
    assert any(f.message == "formula errors: 1" for f in result.findings)
    wb = load_workbook(files[1])
    ws = wb["Summary"]
    ws["B22"] = (
        4  # the seniority count typed over its formula: the value is right, the formula gone
    )
    wb.save(files[1])
    result, _ = run_workbook_checks(files[1:2], bundle, office)
    assert result.status == PASS  # a typed value is not a lost formula in the delivered file
    assert result.findings == []


@office_only
def test_a_missing_block_is_a_finding(bundle: dict[str, Any], tmp_path: Path, office: Any) -> None:
    files = write_workbooks(tmp_path, bundle)
    wb = load_workbook(files[1])
    wb["Summary"]["A22"] = "Seniority"  # the Meets seniority rule label is gone
    wb.save(files[1])
    result, _ = run_workbook_checks(files[1:2], bundle, office)
    assert result.status == FAIL
    assert any("Meets seniority rule row not found" in f.message for f in result.findings)
