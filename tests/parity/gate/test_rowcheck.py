"""Every table row renders in the PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from parity.gate.report import FAIL, PASS, SKIP
from parity.gate.rowcheck import rows_missing, run_rowcheck
from tests.parity.gate.conftest import PREFIX, make_docx, office_only

ROWS = [["Name", "Title"], ["Ada Quorum", "CIO"], ["Bram Tessel", "VP Commercial Systems"]]


def test_rows_found_and_missing_in_a_flattened_text(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / "t.docx", ["Intro"], [ROWS])
    data = docx.read_bytes()
    assert rows_missing(data, "Name  Title\nAda   Quorum CIO\nBram Tessel VP") == (3, 3, [])
    expected, found, missing = rows_missing(data, "Name Title\nAda Quorum CIO")
    assert (expected, found, missing) == (3, 2, [(0, 2)])


def test_without_a_converter_the_check_is_skipped(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / "t.docx", ["Intro"], [ROWS])
    assert run_rowcheck([docx], None).status == SKIP


class _LostRow:
    def pdf_text(self, docx: bytes) -> str | None:
        return "Name Title Ada Quorum CIO"

    def recalculated(self, xlsx: bytes) -> bytes | None:
        return None


def test_a_lost_row_fails(tmp_path: Path) -> None:
    docx = make_docx(tmp_path / f"{PREFIX}_Account_Summary.docx", ["Intro"], [ROWS])
    result = run_rowcheck([docx], _LostRow())
    assert result.status == FAIL
    assert result.details[docx.name] == {"rows_expected": 3, "rows_found": 2}
    assert "table 0 row 2" in result.findings[0].message


@office_only
def test_a_long_table_renders_every_row(tmp_path: Path, office: Any) -> None:
    rows = [["Key", "Value"]] + [[f"Row{i:03d} alpha beta", "x" * 120] for i in range(80)]
    docx = make_docx(tmp_path / f"{PREFIX}_Account_Summary.docx", ["Intro"], [rows])
    result = run_rowcheck([docx], office)
    assert result.status == PASS
    assert result.details[docx.name] == {"rows_expected": 81, "rows_found": 81}
