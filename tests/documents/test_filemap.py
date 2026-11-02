"""The workspace file map names every table and every column, whatever the size (Ask redesign
phase 1, 2026-11-02).

GUARANTEE(test_the_file_map_names_every_table_and_every_column)
"""

from __future__ import annotations

from uuid import uuid4

from documents import filemap
from documents.tables import ColumnInfo, TableInfo


def _table(name: str, columns: list[ColumnInfo], rows: int = 645, member: str | None = None):
    return TableInfo(
        id=uuid4(),
        name=name,
        table=f"Book.xlsx:{name}",
        member=member,
        document="Book.xlsx",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=tuple(columns),
        business_key="job_id",
        row_count=rows,
    )


JOBS = _table(
    "Jobs",
    [
        ColumnInfo("job_id", "Job ID", "text", False),
        ColumnInfo("status", "Status", "text", False, values=("Open", "Closed")),
        ColumnInfo("first_seen", "First seen", "date", False),
        ColumnInfo("description", "Description", "text", True),
        ColumnInfo("platforms", "Platforms", "text", False, multi_valued=True),
    ],
)
TEAMS = _table("Teams", [ColumnInfo("team", "Team", "text", False)], rows=40)


def test_the_file_map_names_every_table_and_every_column() -> None:
    text = filemap.render([JOBS, TEAMS])
    assert text.startswith("<workspace_files>") and text.endswith("</workspace_files>")
    assert '"Jobs"' in text and '"Teams"' in text and "645 rows, 5 columns" in text
    for column in (*JOBS.columns, *TEAMS.columns):
        assert column.name in text
    assert "key: job_id" in text
    assert "values: Open | Closed" in text
    assert "long text: read_rows" in text and "many per cell" in text
    assert 'header "Job ID"' in text


def test_a_workspace_without_a_file_has_no_map() -> None:
    assert filemap.render([]) == ""


def test_prose_documents_are_listed_and_tabular_members_are_not_repeated() -> None:
    sources = [
        {"document": "Call.vtt", "shape": "transcript", "passages": 12, "pages": None},
        {"document": "Plan.docx", "shape": "prose", "passages": 30, "pages": 9},
        {"document": "Book.xlsx", "shape": "tabular", "passages": 645, "member": "Jobs"},
    ]
    text = filemap.render([JOBS], sources)
    assert "DOCUMENT (transcript) — Call.vtt — 12 passages" in text
    assert "DOCUMENT (prose) — Plan.docx — 9 pages" in text
    assert "DOCUMENT (tabular)" not in text


def test_a_big_map_drops_values_and_headers_before_any_table_or_column() -> None:
    wide = [
        _table(
            f"Sheet{i}",
            [
                ColumnInfo(f"col_{i}_{j}", f"Column {j}", "text", False, values=("a", "b", "c"))
                for j in range(30)
            ],
        )
        for i in range(12)
    ]
    full = filemap.render(wide, max_chars=10**9)
    small = filemap.render(wide, max_chars=len(full) - 1)
    assert len(small) < len(full) and "values:" not in small
    for t in wide:
        assert f'"{t.name}"' in small
        assert all(c.name in small for c in t.columns)


def test_the_map_is_the_same_text_for_the_same_workspace() -> None:
    assert filemap.render([JOBS, TEAMS]) == filemap.render([JOBS, TEAMS])
