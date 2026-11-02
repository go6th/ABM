"""The research results as tables Ask can query whole (Ask redesign phase 2, 2026-11-02).

A finished research run materialises the five client tables of its delivered files (People, Job
postings, Tech stack and teams, Facts by grade, Coverage) in ``view_rows``, row for row
(``views.materialize``). Ask used to see them only as a ranked handful of numbered rows
(``MAX_ROWS``), so "list every job posting" over a research result was a sample. Each kind is now
one table of the store's table tools — ``describe_table``, ``query_table``, ``read_rows`` — with the
rows of every account in scope, the same complete sets an uploaded spreadsheet gives:

* the rows are the delivered run's, under the tenant's CURRENT exclusion set (a table built under
  another list is not offered, as the web sheets do);
* an ``account`` column (the account's name) and a ``file_row`` column (the row's place in the
  delivered file) are added, so a workspace-wide question and a citation by row number work;
* one query lists the newest delivered run of every account in scope, however many accounts, so
  a turn pays a fixed number of statements.

Column types come from the export configuration (``views.tables``): ``date`` cells are ISO dates
(a missing one is blank), ``count`` cells are numbers, the rest text; a long-text column is a
payload column (``read_rows`` shows it, a query leaves it out).

GUARANTEE(test_research_results_are_offered_as_complete_tables)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text

from documents.chunking import column_aliases
from documents.tables import ColumnInfo, TableInfo, ViewSource
from views.tables import TABLES, specs

TITLES = {
    "people": "People",
    "jobs": "Job postings",
    "tech": "Tech stack and teams",
    "facts": "Facts by grade",
    "coverage": "Coverage tracker",
}
_TYPE = {"date": "date", "count": "number"}
# label columns whose cells are ISO dates (``exports.readers.jobs``: a posting's first and last
# seen day, blank when unknown); typed as dates so a "since" filter compares dates
_ISO_DATES = frozenset({("jobs", "first_seen_text"), ("jobs", "last_seen_text")})
_NAMESPACE = UUID("6f2c1d3e-52a0-4e0e-9b7c-1f0c5b2d7a11")


def _column(
    key: str, header: str, kind: str, payload: bool = False, *, iso_date: bool = False
) -> ColumnInfo:
    kind_name = "date" if iso_date else _TYPE.get(kind, "text")
    return ColumnInfo(
        key, header, kind_name, payload, False, tuple(column_aliases(header, key, kind_name))
    )


async def research_tables(session: Any, accounts: Sequence[UUID]) -> list[TableInfo]:
    """The research tables of these accounts (their newest delivered runs), one per kind."""
    if not accounts:
        return []
    from views.materialize import current_exclusion_hash

    found = await session.execute(
        text(
            "WITH latest AS (SELECT DISTINCT ON (d.account_id) d.account_id, d.run_id"
            " FROM deliverables d WHERE d.account_id = ANY(:accts) AND d.run_id IS NOT NULL"
            " ORDER BY d.account_id, d.built_at DESC)"
            " SELECT l.account_id, l.run_id, v.table_kind, count(*) AS n FROM latest l"
            " JOIN view_materializations m ON m.run_id = l.run_id AND m.status = 'ready'"
            " AND coalesce(m.exclusion_hash, 'none') = :hash"
            " JOIN view_rows v ON v.run_id = l.run_id AND v.account_id = l.account_id"
            " GROUP BY l.account_id, l.run_id, v.table_kind"
        ),
        {"accts": list(accounts), "hash": await current_exclusion_hash(session)},
    )
    by_kind: dict[str, list[Any]] = defaultdict(list)
    for r in found.all():
        by_kind[str(r.table_kind)].append(r)
    spec = specs()
    out: list[TableInfo] = []
    for kind in TABLES:
        rows = by_kind.get(kind)
        if not rows or kind not in TITLES:
            continue
        runs = tuple(sorted(UUID(str(r.run_id)) for r in rows))
        accts = tuple(sorted(UUID(str(r.account_id)) for r in rows))
        columns = (
            _column("account", "Account", "text_short"),
            _column("file_row", "File row", "count"),
            *(
                _column(
                    c.key,
                    c.header,
                    c.kind,
                    c.kind == "text_long",
                    iso_date=(kind, c.key) in _ISO_DATES,
                )
                for c in spec[kind].exported_columns
            ),
        )
        out.append(
            TableInfo(
                id=uuid5(_NAMESPACE, f"{kind}:{','.join(map(str, runs))}"),
                name=f"Research: {TITLES[kind]}",
                table=f"Research:{TITLES[kind]}",
                member=None,
                document="the research results",
                document_id=runs[0],
                account_id=accts[0],
                columns=columns,
                business_key=None,
                row_count=sum(int(r.n) for r in rows),
                view=ViewSource(kind, runs, accts),
            )
        )
    return out
