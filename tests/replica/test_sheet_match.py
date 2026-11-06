"""An Ask citation opens the deep-dive sheet, narrowed to the rows it counted (owner,
2026-11-04; ``docs/plans/ingestion-hardening-ask.md``). SYNTHETIC rows and a fake model, no
database.

* ``sheets.parse`` takes a match only on a matchable column of THAT sheet, with a bounded value;
  the predicate is fixed SQL with the value bound and escaped for LIKE.
* The delivered workbook's own table (``file_tabs``) matches on the column whose header is the
  sheet column's label, and its chips count within the match.
* A cited table that is a deliverable workbook of a workspace zip (or a research table) cites the
  sheet, with the answer's own single filter, never a ``url``; the stored answer keeps it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.api.contract import AnswerSource
from account_intelligence.replica import answers as A
from account_intelligence.replica import file_tabs as FT
from account_intelligence.replica import sheets as SH
from account_intelligence.replica.model_answer import stream_with_model
from account_intelligence.replica.source_label import relabel_answer
from documents import tables as T
from documents.tables import ColumnInfo, TableInfo, ViewSource

# ---- sheets.parse ----------------------------------------------------------------------------


def test_parse_takes_a_match_on_a_matchable_column_of_that_sheet() -> None:
    q = SH.parse("jobs", None, None, None, match_col="location", match="  San Francisco ")
    assert q.match is not None
    col, value = q.match
    assert (col.key, col.label, value) == ("location", "Location", "San Francisco")
    assert SH.match_out(q) == {"col": "location", "label": "Location", "value": "San Francisco"}
    assert SH.parse("jobs", None, None, None).match is None
    assert SH.match_out(SH.parse("jobs", None, None, None)) is None
    # every live sheet the spec names has its matchable columns, and nothing else does
    marked = {k: {c.key for c in s.cols if c.match} for k, s in SH.SHEETS.items()}
    assert marked["people"] == {"name", "title", "area", "priority"}
    assert marked["jobs"] == {"title", "lanes", "location", "status"}
    assert marked["technology"] == {"technology"}
    assert marked["news"] == {"headline", "category"}
    assert marked["sources"] == {"title", "publisher"}
    assert marked["teams"] == {"team", "function", "locations"}
    assert marked["connections"] == {"company", "domain", "category"}
    assert not marked["signals"] and not marked["calls"] and not marked["mail-and-drive"]


@pytest.mark.parametrize(
    ("match_col", "match"),
    [
        ("link", "x"),  # a column of the sheet that is not matchable
        ("posted", "2026"),  # a date column
        ("grade", "Confirmed"),  # not a column of the Jobs sheet at all
        ("nope", "x"),
        ("location", "x" * 201),  # over the bound
        ("location", "San\x00Francisco"),
        ("location", "   "),  # a column without a value
        (None, "San Francisco"),  # a value without a column
    ],
)
def test_parse_refuses_a_match_outside_the_allow_list(match_col: str | None, match: str) -> None:
    with pytest.raises(SH.BadSheetQuery):
        SH.parse("jobs", None, None, None, match_col=match_col, match=match)


def test_a_match_names_a_column_of_the_sheet_it_opens() -> None:
    # "location" is matchable on Job postings, not on People's live sheet
    with pytest.raises(SH.BadSheetQuery):
        SH.parse("people", None, None, None, match_col="location", match="Durham")
    # the file-shaped sheets mark their text columns: the files' People list has a Location
    q = SH.parse("people", None, None, None, run_id=uuid4(), match_col="location", match="Durham")
    assert q.match is not None and q.match[0].key == "location"
    file_jobs = {c.key: c.match for c in SH.file_sheet("jobs").cols}
    assert file_jobs["location"] and file_jobs["title"] and file_jobs["status"]
    assert not file_jobs["url"] and not file_jobs["description"] and not file_jobs["source_code"]


def test_the_match_is_fixed_sql_with_the_value_bound_and_escaped() -> None:
    assert SH.like_pattern("San Francisco") == "%San Francisco%"
    assert SH.like_pattern("100%_off\\") == "%100\\%\\_off\\\\%"
    q = SH.parse("jobs", None, None, None, match_col="location", match="50%")
    assert SH.match_where(q) == "CAST(s.location AS text) ILIKE :match ESCAPE '\\'"
    bound = SH.params(q, uuid4(), "u", datetime.now(UTC), tenant_id=uuid4())
    assert bound["match"] == "%50\\%%"
    # a file-shaped sheet matches on the file's text cell, never the sort's json
    fq = SH.parse("jobs", None, None, None, run_id=uuid4(), match_col="location", match="x")
    assert SH.match_where(fq) == "CAST(s.\"location\" AS text) ILIKE :match ESCAPE '\\'"
    assert SH.match_where(SH.parse("jobs", None, None, None)) == "true"
    assert "match" not in SH.params(
        SH.parse("jobs", None, None, None), uuid4(), "u", datetime.now(UTC), tenant_id=uuid4()
    )


# ---- the delivered workbook's own table ----------------------------------------------------


def _tab() -> FT.Tab:
    rows = (
        (FT.Cell("Analyst"), FT.Cell("San Francisco, US"), FT.Cell("Open")),
        (FT.Cell("Engineer"), FT.Cell("Durham, US"), FT.Cell("Open")),
        (FT.Cell("Lead"), FT.Cell("south san francisco"), FT.Cell("Closed")),
    )
    table = FT.Block("table", header=("Title", "Location", "Status"), rows=rows)
    return FT.Tab("jobs", "Jobs", (table,), main=True)


def test_the_files_own_table_matches_on_the_column_with_the_sheets_label() -> None:
    tab = _tab()
    q = FT.grid_query(tab, SH.SHEETS["jobs"], None, None, 0, 50, None, "location", "SAN FRAN")
    page = FT.grid_page(tab, q)
    assert [r["cells"]["c1"] for r in page["rows"]] == ["Analyst", "Lead"]
    assert page["total"] == 2
    assert page["match"] == {"col": "location", "label": "Location", "value": "SAN FRAN"}
    counts = {f["key"]: f["count"] for f in page["filters"]}
    assert counts["all"] == 2  # within the match
    assert [r["n"] for r in FT.grid_rows(tab, q)] == [1, 2]
    assert FT.grid_cell(tab, q, 2, "c1") == {"n": 2, "col": "c1", "text": "Lead", "href": None}
    # the live sheet's "Role" and the files' "Title" are the same column
    q = FT.grid_query(tab, SH.SHEETS["jobs"], None, None, 0, 50, None, "title", "eng")
    assert [r["cells"]["c1"] for r in FT.grid_page(tab, q)["rows"]] == ["Engineer"]
    assert FT.grid_query(tab, SH.SHEETS["jobs"], None, None, 0, 50).match is None
    for col, value in (("lanes", "Salesforce"), ("posted", "2026"), ("location", "x" * 201)):
        with pytest.raises(SH.BadSheetQuery):
            FT.grid_query(tab, SH.SHEETS["jobs"], None, None, 0, 50, None, col, value)


# ---- the citation ---------------------------------------------------------------------------

MEMBER = "holdings/deliverables/Holdings_Job_Postings.xlsx"


def _acct() -> A.Acct:
    return A.Acct(uuid4(), "holdings.example", "Synthetic Holdings", [], None, None, 1)


def _jobs_table(acct: A.Acct, member: str | None = MEMBER, view: ViewSource | None = None) -> Any:
    columns = (
        ColumnInfo("title", "Title", "text", False),
        ColumnInfo("location", "Location", "text", False),
        ColumnInfo("status", "Status", "text", False),
        ColumnInfo("platforms_named", "Platforms named", "text", False),
    )
    return TableInfo(
        id=uuid4(),
        name="Jobs",
        table=f"{member}:Jobs",
        member=member,
        document="Holdings_workspace.zip",
        document_id=uuid4(),
        account_id=acct.id,
        columns=columns,
        business_key=None,
        row_count=644,
        view=view,
    )


def test_a_tables_sheet_comes_from_its_zip_role_or_its_research_kind() -> None:
    acct = _acct()
    assert A.member_role(MEMBER) == "deliverable_jobs"
    assert A.table_sheet(_jobs_table(acct)) == "jobs"
    people = "holdings/deliverables/Holdings_People_List.xlsx"
    tech = "Holdings/Deliverables/x/Holdings_Tech_Stack_and_Teams.xlsx"
    assert A.table_sheet(_jobs_table(acct, people)) == "people"
    assert A.table_sheet(_jobs_table(acct, tech)) == "technology"
    # a raw pull, a build twin, an owner-only file and a loose upload are not sheets
    for member in (
        "holdings/data/jobs.json",
        "holdings/build/jobs_twin.json",
        "holdings/deliverables/Holdings_Owner_Only.docx",
        "Holdings_Job_Postings.xlsx",
        None,
    ):
        assert A.table_sheet(_jobs_table(acct, member)) is None, member
    view = ViewSource("tech", (uuid4(),), (acct.id,))
    assert A.table_sheet(_jobs_table(acct, None, view)) == "technology"
    rows = A.table_rows(A.Workspace([acct], [], [], [], [], [], [], {}, False), [_jobs_table(acct)])
    assert rows[0].extra["sheet"] == {"key": "jobs"}
    # the sheet link is not part of what a question matches the row on
    assert "{'key'" not in rows[0].haystack()


def _stored(table: Any, where: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    query = T.parse(table, {"where": where, **extra})
    return {"q1": (table, query, {})}


def _row(table: Any, acct: A.Acct) -> A.Row:
    (row,) = A.table_rows(A.Workspace([acct], [], [], [], [], [], [], {}, False), [table])
    return row


@pytest.mark.parametrize(
    ("where", "extra", "expected"),
    [
        ([{"column": "location", "op": "icontains", "value": "San Francisco"}], {},
         {"match_col": "location", "match": "San Francisco"}),
        ([{"column": "Location", "op": "eq", "value": "Durham, US"}], {},
         {"match_col": "location", "match": "Durham, US"}),
        ([{"column": "title", "op": "contains", "value": "Analyst"}], {},
         {"match_col": "title", "match": "Analyst"}),
        # two conditions, another operator, a radius, a column with no matchable sheet column
        ([{"column": "location", "op": "icontains", "value": "San Francisco"},
          {"column": "status", "op": "eq", "value": "Open"}], {}, {}),
        ([{"column": "location", "op": "neq", "value": "Durham"}], {}, {}),
        ([{"column": "location", "op": "icontains", "value": "Durham"}], {"radius_miles": 25}, {}),
        ([{"column": "platforms_named", "op": "icontains", "value": "Salesforce"}], {}, {}),
        ([], {}, {}),
    ],
)  # fmt: skip
def test_the_citation_carries_the_answers_one_filter(
    where: list[dict[str, Any]], extra: dict[str, Any], expected: dict[str, str]
) -> None:
    acct = _acct()
    table = _jobs_table(acct)
    row = _row(table, acct)
    A.carry_table_matches([row], _stored(table, where, **extra))
    out = A.source_out(1, row)
    assert out["url"] is None and out["label"] is None
    assert out["title"] == "Job postings sheet"
    assert out["sheet"] == {
        "key": "jobs",
        "match_col": expected.get("match_col"),
        "match": expected.get("match"),
    }
    if expected:
        assert out["meta"] == f"Filtered: {out['sheet']['match_col'].title()} contains" + (
            f" {expected['match']}"
        )
    else:
        assert out["meta"] == row.meta
    AnswerSource.model_validate(out)  # the strict contract takes it


def test_the_last_query_of_the_table_wins_and_another_tables_query_is_not_used() -> None:
    acct = _acct()
    table, other = _jobs_table(acct), _jobs_table(acct)
    row = _row(table, acct)
    first = T.parse(table, {"where": [{"column": "location", "op": "eq", "value": "Durham"}]})
    last = T.parse(table, {"where": [{"column": "title", "op": "eq", "value": "Lead"}]})
    elsewhere = T.parse(other, {"where": [{"column": "status", "op": "eq", "value": "Open"}]})
    A.carry_table_matches(
        [row], {"q1": (table, first, {}), "q2": (table, last, {}), "q3": (other, elsewhere, {})}
    )
    assert row.extra["sheet"]["match_col"] == "title" and row.extra["sheet"]["match"] == "Lead"


def test_a_research_table_maps_its_columns_by_key() -> None:
    acct = _acct()
    view = ViewSource("jobs", (uuid4(),), (acct.id,))
    table = _jobs_table(acct, None, view)
    row = _row(table, acct)
    assert row.extra["sheet"] == {"key": "jobs"}
    A.carry_table_matches(
        [row], _stored(table, [{"column": "location", "op": "icontains", "value": "Poznań"}])
    )
    assert A.source_out(2, row)["sheet"] == {
        "key": "jobs",
        "match_col": "location",
        "match": "Poznań",
    }


def test_a_stored_answer_keeps_its_sheet_link_when_reopened() -> None:
    source = {
        "n": 1,
        "title": "Job postings sheet",
        "meta": "Filtered: Location contains San Francisco",
        "date": None,
        "url": None,
        "label": None,
        "kind": "file",
        "account_domain": "holdings.example",
        "account_name": "Synthetic Holdings",
        "sheet": {"key": "jobs", "match_col": "location", "match": "San Francisco"},
    }
    (out,) = relabel_answer({"sources": [source]})["sources"]
    assert out["sheet"] == source["sheet"] and out["url"] is None


class _Fake:
    """A model that answers with one citation of the table (no paid call)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def stream(self, *_a: Any, **_k: Any):  # type: ignore[no-untyped-def]
        yield type("E", (), {"kind": "text", "text": self.reply, "usage": None, "tool": None})()
        yield type("E", (), {"kind": "usage", "text": "", "usage": None, "tool": None})()


@pytest.mark.asyncio
async def test_an_answer_about_an_imported_workspace_cites_the_jobs_sheet_with_its_match() -> None:
    """The Ask answer path (``api.routes.ask``): table rows, the model's reply, the stored
    query's filter carried onto the citation, then the numbered sources."""
    acct = _acct()
    table = _jobs_table(acct)
    ws = A.Workspace([acct], [], [], [], [], [], [], {}, False)
    ws.tables = A.table_rows(ws, [table])
    q, local = A.draft_local(
        "How many job openings are there in San Francisco?", ws, datetime.now(UTC)
    )
    draft = local
    async for kind, value in stream_with_model(
        _Fake("There are **12** openings in San Francisco [R1]."),
        object(),
        q,
        ws,
        local,
        user_id="u",
        request_id="r",
    ):
        if kind == "done":
            draft = value.draft
    stored = _stored(table, [{"column": "location", "op": "icontains", "value": "San Francisco"}])
    A.carry_table_matches(draft.rows.values(), stored)
    answer = A.finish(draft, q, ws, datetime.now(UTC), None)
    (source,) = answer["sources"]
    assert source["sheet"] == {"key": "jobs", "match_col": "location", "match": "San Francisco"}
    assert source["url"] is None
    assert source["title"] == "Job postings sheet"
    assert source["meta"] == "Filtered: Location contains San Francisco"
    assert source["account_domain"] == "holdings.example"
    AnswerSource.model_validate(source)


def test_a_research_table_of_several_accounts_is_cited_without_a_sheet_link() -> None:
    """Review B1 (2026-11-04): a workspace-wide research table spans accounts; its ``account_id``
    is only the first, so a link would open another account's sheet. It gets none."""
    acct, other = _acct(), _acct()
    view = ViewSource("jobs", (uuid4(), uuid4()), (acct.id, other.id))
    (row,) = A.table_rows(
        A.Workspace([acct, other], [], [], [], [], [], [], {}, False),
        [_jobs_table(acct, None, view)],
    )
    assert "sheet" not in row.extra
    assert A.source_out(1, row)["sheet"] is None


@pytest.mark.parametrize(
    ("where", "extra"),
    [
        # the answer counted distinct postings: the sheet would list every copy
        ([{"column": "location", "op": "icontains", "value": "San Francisco"}],
         {"distinct_on": "title"}),
        # a value the answer gate would refuse is never printed in the source's line
        ([{"column": "location", "op": "icontains", "value": "San Francisco – Bay"}], {}),
    ],
)  # fmt: skip
def test_a_filter_the_sheet_cannot_repeat_exactly_is_not_carried(
    where: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    acct = _acct()
    table = _jobs_table(acct)
    row = _row(table, acct)
    A.carry_table_matches([row], _stored(table, where, **extra))
    out = A.source_out(1, row)
    assert out["sheet"] == {"key": "jobs", "match_col": None, "match": None}
