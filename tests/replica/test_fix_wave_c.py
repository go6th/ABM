"""Fix wave C (2026-10-29), website side: the stored-table rows colour like the file."""

from __future__ import annotations

from account_intelligence.replica import sheets as SH


def _query(table: str, keys: tuple[str, ...]) -> SH.Query:
    cols = tuple(SH.Col(k, k.title(), None, "text", None) for k in keys)
    sheet = SH.Sheet(table, table.title(), "f.xlsx", "", "", cols, (), "x", table=table)
    return SH.Query(sheet, SH.Filter("all", "All", "true"), cols[0], False, 0, 10)


def test_a_new_posting_row_is_coloured_like_the_file() -> None:
    q = _query("jobs", ("title", "new_flag"))
    new = SH.row_out(q, 1, {"title": "A", "new_flag": "Yes", "link": None}, None)  # type: ignore[arg-type]
    old = SH.row_out(q, 2, {"title": "B", "new_flag": "", "link": None}, None)  # type: ignore[arg-type]
    assert new["fills"] == {"title": "new_row", "new_flag": "new_row"}
    assert "fills" not in old


def test_a_priority_cell_is_coloured_by_its_letter() -> None:
    q = _query("people", ("name", "priority"))
    row = SH.row_out(q, 1, {"name": "N", "priority": "A", "link": None}, None)  # type: ignore[arg-type]
    assert row["fills"] == {"priority": "priority_a"}
    other = SH.row_out(q, 2, {"name": "M", "priority": "", "link": None}, None)  # type: ignore[arg-type]
    assert "fills" not in other


def test_a_cell_over_excels_limit_is_cut_with_a_note_and_counts_utf16_units() -> None:
    from account_intelligence.replica import table_export as TE

    assert TE.fit_cell("short") == "short" and TE.fit_cell(5) == 5
    ascii_text = "x" * 40_000
    emoji_text = "\U0001f680" * 30_000  # 60,000 UTF-16 units in 30,000 characters
    for value in (ascii_text, emoji_text):
        cut = TE.fit_cell(value)
        assert cut.endswith(TE.CELL_CUT)
        assert len(cut.encode("utf-16-le")) // 2 <= TE.CELL_LIMIT
    assert TE.fit_cell("x" * 32_767) == "x" * 32_767


def test_the_plain_excel_link_cell_opens_only_a_safe_address() -> None:
    from openpyxl import Workbook

    from account_intelligence.replica import table_export as TE

    ws = Workbook(write_only=True).create_sheet("S")
    assert TE._link_cell(ws, "Open", "https://jobs.example/a").hyperlink is not None
    for bad in ("javascript:alert(1)", "https://jobs.example/a b", "ftp://x.example/a", None):
        assert TE._link_cell(ws, "Open", bad).hyperlink is None
