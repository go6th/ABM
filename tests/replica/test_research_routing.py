"""Research tables must not widen the table route (audit 2026-11-02): they have the same generic
headers in every account, so ordinary account questions stay prose, and the read plan picks the
table the question names. Pure: no database.

GUARANTEE(test_ordinary_account_questions_stay_prose_with_research_tables)
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from account_intelligence.replica import read_plan as RP
from account_intelligence.replica import research_tables as RT
from account_intelligence.replica import router as R
from documents.tables import ColumnInfo, TableInfo, ViewSource
from views.tables import specs


def _research() -> list[TableInfo]:
    out = []
    for kind, title in RT.TITLES.items():
        cols = (
            RT._column("account", "Account", "text_short", routed=False),
            RT._column("file_row", "File row", "count", routed=False),
            *(
                RT._column(c.key, c.header, c.kind, c.kind == "text_long")
                for c in specs()[kind].exported_columns
            ),
        )
        out.append(
            TableInfo(
                uuid4(),
                f"Research: {title}",
                f"Research:{title}",
                None,
                "the research results",
                uuid4(),
                uuid4(),
                cols,
                None,
                100,
                view=ViewSource(kind, (), ()),
            )
        )
    return out


def _uploaded() -> TableInfo:
    cols = (
        ColumnInfo(
            "status", "Status", "text", False, aliases=("status",), values=("Open", "Closed")
        ),
        ColumnInfo("title", "Title", "text", False, aliases=("title",)),
    )
    return TableInfo(
        uuid4(), "Jobs", "Book.xlsx:Jobs", None, "Book.xlsx", uuid4(), uuid4(), cols, None, 50
    )


def _route(question: str, tables: list[TableInfo]) -> str:
    return asyncio.run(R.route(question, tables)).route


@pytest.mark.parametrize(
    "question",
    [
        "What is the account status?",
        "Is the account hiring?",
        "Draft an email to the account owner",
        "What's the latest news on this account?",
        "What technology do they use?",
        "What is the name of their CIO?",
        "What category of buyer are they?",
        "Give me an overview of the account",
        "Who owns Salesforce at Acme?",
        "What does Acme sell?",
    ],
)
def test_ordinary_account_questions_stay_prose_with_research_tables(question: str) -> None:
    assert _route(question, _research()) == "prose"


@pytest.mark.parametrize(
    "question",
    [
        "List all people with title VP",
        "How many job postings mention Salesforce?",
        "Who are the people at Acme?",
        "show tech stack and teams",
        "how many facts are confirmed at this account",
    ],
)
def test_filter_questions_and_questions_naming_a_table_still_route_to_the_tables(
    question: str,
) -> None:
    assert _route(question, _research()) == "tabular"


def test_the_synthetic_columns_never_route_and_uploaded_tables_route_as_before() -> None:
    tables = _research()
    for t in tables:
        assert not any(c.routed for c in t.columns if c.name in ("account", "file_row"))
    # an uploaded sheet's own column and value words still route, with research tables beside it
    assert _route("which jobs are open", [_uploaded(), *tables]) == "tabular"
    assert _route("what status is the account", [_uploaded(), *tables]) == "tabular"  # its column


def test_the_read_plan_picks_the_table_the_question_names_not_the_first_three() -> None:
    tables = _research()
    names = lambda q: [t.name for t in RP.pick_tables(q, tables)]  # noqa: E731
    assert names("List the job postings at this account that mention Salesforce") == [
        "Research: Job postings"
    ]
    assert names("how many facts are confirmed at this account") == ["Research: Facts by grade"]
    assert names("show tech stack and teams") == ["Research: Tech stack and teams"]
    assert names("what is the account status") == []  # generic words alone pick nothing
