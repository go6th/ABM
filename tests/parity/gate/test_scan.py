"""The third-party scan over Word and Excel files."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from parity.gate.report import FAIL, PASS
from parity.gate.scan import run_scan
from tests.parity.gate.conftest import make_docx


def _a_tool_name() -> str:
    from agents.skill_context import tool_names

    names = [n for n in tool_names() if " " not in n]
    if not names:
        pytest.skip("no provider names configured")
    return names[0]


def test_clean_files_pass(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / "A_Account_Summary.docx", ["Research by 6th agents (S1)."])
    wb = Workbook()
    wb.active.append(["Platform", "Count"])
    wb.active.append(["Salesforce", 3])
    xlsx = tmp_path / "A_Job_Postings.xlsx"
    wb.save(str(xlsx))
    result = run_scan([docx, xlsx])
    assert result.status == PASS
    assert result.findings == []


def test_tool_name_in_a_word_run_fails(tmp_path: Path) -> None:
    name = _a_tool_name()
    docx = make_docx(tmp_path / "A_Account_Summary.docx", [f"Pulled with {name} on Monday."])
    result = run_scan([docx])
    assert result.status == FAIL
    assert any("tool_name" in f.message for f in result.findings)
    assert all(name not in f.message for f in result.findings)


def test_tool_name_in_a_cell_fails(tmp_path: Path) -> None:
    name = _a_tool_name()
    wb = Workbook()
    wb.active.append(["Note", f"from {name.lower()}"])
    xlsx = tmp_path / "A_People_List.xlsx"
    wb.save(str(xlsx))
    assert run_scan([xlsx]).status == FAIL


def test_credit_words_warn_only(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / "A_Account_Summary.docx", ["Carbon credits and credit notes."])
    result = run_scan([docx])
    assert result.status == PASS
    assert [f.severity for f in result.findings] == ["warn"]
    assert result.details["files"][docx.name]["credit_words"] == 2
