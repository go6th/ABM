"""Every table row of a Word file appears in its rendered PDF (the port of the skill's
``rowcheck.py``).

A table that crosses a page break can silently lose rows in rendering. For every row of every
table the first cell's first three words must appear in the PDF text (whitespace flattened); as
in the skill, the first word alone also counts, because a long first cell wraps. The result
reports rows expected and rows found per file. Without a converter the check is skipped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from parity.gate.files import Office, docx_table_first_cells, word_files
from parity.gate.report import CheckResult, Finding, result, skipped

NAME = "rowcheck"
_SPACES = re.compile(r"\s+")


def rows_missing(docx: bytes, pdf_text: str) -> tuple[int, int, list[tuple[int, int]]]:
    """``(expected, found, missing)`` where missing lists ``(table index, row index)``."""
    flat = _SPACES.sub(" ", pdf_text)
    expected = found = 0
    missing: list[tuple[int, int]] = []
    for ti, rows in enumerate(docx_table_first_cells(docx)):
        for ri, text in enumerate(rows):
            words = text.split()[:3]
            if not words:
                continue
            expected += 1
            if " ".join(words) in flat or words[0] in flat:
                found += 1
            else:
                missing.append((ti, ri))
    return expected, found, missing


def run_rowcheck(files: list[Path], office: Office | None) -> CheckResult:
    """The row check on every Word file."""
    if office is None:
        return skipped(NAME, "no Word to PDF converter")
    findings: list[Finding] = []
    details: dict[str, Any] = {}
    for path in word_files(files):
        data = path.read_bytes()
        text = office.pdf_text(data)
        if text is None:
            return skipped(NAME, f"conversion failed: {path.name}")
        expected, found, missing = rows_missing(data, text)
        details[path.name] = {"rows_expected": expected, "rows_found": found}
        if missing:
            where = ", ".join(f"table {t} row {r}" for t, r in missing[:10])
            findings.append(
                Finding(
                    NAME, f"rows expected {expected}, found {found}; missing: {where}", path.name
                )
            )
    return result(NAME, findings, details)
