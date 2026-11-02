"""The read plan picks the tables a question names and reads them before the model starts.

GUARANTEE(test_the_read_plan_reads_the_matched_tables_before_the_model_starts)
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from account_intelligence.replica import read_plan as RP
from documents.tables import ColumnInfo, TableInfo


def _table(name: str, columns: list[ColumnInfo], rows: int = 10) -> TableInfo:
    return TableInfo(
        id=uuid4(),
        name=name,
        table=f"Book.xlsx:{name}",
        member=None,
        document="Book.xlsx",
        document_id=uuid4(),
        account_id=uuid4(),
        columns=tuple(columns),
        business_key=None,
        row_count=rows,
    )


JOBS = _table(
    "Jobs",
    [
        ColumnInfo("title", "Title", "text", False, aliases=("title",)),
        ColumnInfo(
            "status", "Status", "text", False, aliases=("status",), values=("Open", "Closed")
        ),
    ],
)
PEOPLE = _table("People", [ColumnInfo("name", "Name", "text", False, aliases=("name",))])
TEAMS = _table("Teams", [ColumnInfo("team", "Team", "text", False, aliases=("team",))])
OTHER = _table("Other", [ColumnInfo("misc", "Misc", "text", False, aliases=("misc",))])


def test_the_read_plan_reads_the_matched_tables_before_the_model_starts() -> None:
    # the table whose column or stored value the question names, and no other
    assert RP.pick_tables("list the open jobs by title", [PEOPLE, JOBS, TEAMS]) == [JOBS]
    # best match first, at most MAX_TABLES
    both = RP.pick_tables("team name and title and status", [JOBS, PEOPLE, TEAMS, OTHER])
    assert both[0] is JOBS and len(both) <= RP.MAX_TABLES
    # nothing named: a workspace of two tables needs no matching, a bigger one plans nothing
    assert RP.pick_tables("what is going on", [JOBS, PEOPLE]) == [JOBS, PEOPLE]
    assert RP.pick_tables("what is going on", [JOBS, PEOPLE, TEAMS]) == []
    assert RP.pick_tables("anything", []) == []


def test_a_table_is_rendered_whole_one_line_per_row_with_every_cell() -> None:
    rows = [
        SimpleNamespace(row_no=1, cells={"title": "Salesforce\nDeveloper", "status": "Open"}),
        SimpleNamespace(row_no=2, cells={"title": "Analyst"}),
    ]
    assert RP.render_whole(JOBS, rows) == (
        "row | title | status\n1 | Salesforce Developer | Open\n2 | Analyst | "
    )
