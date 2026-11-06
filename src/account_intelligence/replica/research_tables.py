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

An account without a delivered, materialised run still has rows: an uploaded workspace zip is
imported into the native ``people``, ``job_postings`` and ``tech_detections`` tables under a
file-import run, which no export reader materialises (UCB 2026-11-04: six Ask turns ran with no
count tools over 1,600 imported people and 767 postings, and answered "0 open Salesforce
postings" from a stale count file). For an account that has a file-import run and no
materialised tables at all, the same three kinds are offered from the native rows
(``ViewSource.source == "native"``, ``documents.repo.rows_source``): the account's CURRENT rows
(newest per person or posting key), without the rows the tenant's current exclusions hide, with
the same tools. A researched account is never read this way: its rows reach Ask only through the
materialised tables, which the export rules and the quality gate have passed (a withheld or
stale materialisation offers nothing, as the files do). When an account in scope has
materialised tables and another has only imported rows, the imported rows are a second table of
the kind, named ``(imported)``.

GUARANTEE(test_research_results_are_offered_as_complete_tables)
GUARANTEE(test_an_imported_workspace_is_offered_as_research_tables)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text

from documents import repo as DOCREPO
from documents.chunking import VALUE_INDEX_LIMIT, column_aliases
from documents.import_status import STALE_RUNNING_S
from documents.tables import ColumnInfo, TableInfo, ViewSource
from obs.logging import logger
from views.tables import TABLES, specs

_log = logger(__name__)

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
    key: str,
    header: str,
    kind: str,
    payload: bool = False,
    *,
    iso_date: bool = False,
    routed: bool = True,
) -> ColumnInfo:
    kind_name = "date" if iso_date else _TYPE.get(kind, "text")
    return ColumnInfo(
        key,
        header,
        kind_name,
        payload,
        False,
        tuple(column_aliases(header, key, kind_name)),
        routed=routed,
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
            _column("account", "Account", "text_short", routed=False),
            _column("file_row", "File row", "count", routed=False),
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
                # how many of the accounts in scope have a delivered run: an account without one
                # is not in this table, and the model should be able to say so
                document=f"the research results ({len(accts)} of {len(accounts)} accounts)",
                document_id=runs[0],
                account_id=accts[0],
                columns=columns,
                business_key=None,
                row_count=sum(int(r.n) for r in rows),
                view=ViewSource(kind, runs, accts),
            )
        )
    covered = {
        t.view.kind: set(t.view.account_ids) for t in out if t.view is not None
    }  # fmt: skip
    try:
        out.extend(await native_tables(session, accounts, covered))
    except Exception:  # noqa: BLE001 - the materialised tables still answer
        _log.warning("research_tables.native_failed", exc_info=True)
    return out


# The native rows as the model sees them (``documents.repo._NATIVE_ROWS`` builds the cells):
# key, header, type, payload, multi-valued. Dates are ISO text, blank when unknown.
_NATIVE_COLUMNS: dict[str, tuple[tuple[str, str, str, bool, bool], ...]] = {
    "people": (
        ("name", "Name", "text", False, False),
        ("title", "Title", "text", False, False),
        ("level", "Level", "text", False, False),
        ("location", "Location", "text", False, False),
        ("lanes", "Lanes", "text", False, True),
        ("priority", "Priority", "text", False, False),
        ("scope", "Scope", "text", False, False),
        ("origin", "Found via", "text", False, False),
        ("profile_url", "LinkedIn", "text", False, False),
        ("former_employers", "Former employers", "text", False, True),
        ("headline", "Headline", "text", True, False),
        ("note", "Note", "text", True, False),
    ),
    "jobs": (
        ("requisition", "Requisition", "text", False, False),
        ("title", "Title", "text", False, False),
        ("status", "Status", "text", False, False),
        ("location", "Location", "text", False, False),
        ("platforms", "Platforms named", "text", False, True),
        ("lanes", "Lanes", "text", False, True),
        ("class", "Class", "text", False, False),
        ("seniority", "Seniority", "text", False, False),
        ("posted", "Posted", "date", False, False),
        ("first_seen", "First seen", "date", False, False),
        ("last_seen", "Last seen", "date", False, False),
        ("entity", "Entity", "text", False, False),
        ("url", "Posting URL", "text", False, False),
        ("summary", "What the posting says", "text", True, False),
    ),
    "tech": (
        ("technology", "Technology", "text", False, False),
        ("sources", "Sources", "number", False, False),
        ("cross_check", "Cross check", "text", False, False),
        ("first_seen", "First seen", "text", False, False),
        ("last_seen", "Last seen", "text", False, False),
    ),
}
# the low-cardinality columns whose distinct values route a question ("Open", "Salesforce")
_NATIVE_VALUES: dict[str, tuple[str, ...]] = {
    "people": ("level", "priority", "scope", "lanes"),
    "jobs": ("status", "class", "platforms", "lanes", "seniority"),
    "tech": ("cross_check",),
}


def _native_column(key: str, header: str, kind: str, payload: bool, multi: bool) -> ColumnInfo:
    return ColumnInfo(key, header, kind, payload, multi, tuple(column_aliases(header, key, kind)))


async def _hidden(session: Any, kind: str, accounts: Sequence[UUID]) -> tuple[str, ...] | None:
    """The ids of the native rows the tenant's CURRENT exclusions hide (the archive's text rule,
    ``exports.readers.exclusions.text_excluded``, as the live sheets apply it): a person the
    tenant excluded by name or profile address, a posting on an excluded company's domain. None
    when the stored settings no longer validate (nothing is offered then: fail closed)."""
    from exports.readers.exclusions import text_excluded, with_known_names
    from tenancy import settings as tenant_settings

    try:
        rule = tenant_settings.ExclusionFilter.of((await tenant_settings.current(session)).settings)
    except tenant_settings.SettingsInvalid:
        return None
    if not rule.people and not rule.companies:
        return ()
    if kind == "people":
        rows = (
            await session.execute(
                text(
                    "SELECT id, name, profile_url, title, headline, location FROM people"
                    " WHERE tenant_id = app_tenant() AND account_id = ANY(:accts)"
                    " AND erased_at IS NULL"
                ),
                {"accts": list(accounts)},
            )
        ).all()
        rule = with_known_names(rule, [(r.name, r.profile_url) for r in rows])
        return tuple(
            str(r.id)
            for r in rows
            if text_excluded(rule, r.name, r.title, r.headline, r.location, url=r.profile_url)
        )
    if kind == "jobs":
        rows = (
            await session.execute(
                text(
                    "SELECT id, title, url, summary_read FROM job_postings"
                    " WHERE tenant_id = app_tenant() AND account_id = ANY(:accts)"
                ),
                {"accts": list(accounts)},
            )
        ).all()
        return tuple(
            str(r.id) for r in rows if text_excluded(rule, r.title, r.summary_read, url=r.url)
        )
    return ()


def _distinct(found: Any, multi: bool) -> tuple[str, ...]:
    """The distinct values of a column as the router's lexicon (the items of a multi-valued
    cell one by one), or none when the column is not low-cardinality."""
    items: set[str] = set()
    for value in found or ():
        if not value:
            continue
        for part in str(value).split(", ") if multi else (str(value),):
            if part.strip():
                items.add(part.strip())
    if 0 < len(items) <= VALUE_INDEX_LIMIT:
        return tuple(sorted(items, key=str.casefold))
    return ()


async def imported_accounts(session: Any, accounts: Sequence[UUID]) -> tuple[UUID, ...]:
    """The accounts in scope whose rows are an import's: a file-import run (``runs.lifecycle``)
    and no materialisation of any run, ready, withheld or stale (a researched account's rows are
    read only through its materialised tables, module docstring). An account whose import is
    still writing (``documents.import_status``: a run pending or running, started within the
    stale-running window) is left out until it finishes: a count over half-written rows would be
    wrong, and the next turn sees the whole import."""
    if not accounts:
        return ()
    found = await session.execute(
        text(
            "SELECT DISTINCT r.account_id FROM account_runs r WHERE r.tenant_id = app_tenant()"
            " AND r.account_id = ANY(:accts) AND r.finished_reason = 'file_import'"
            " AND NOT EXISTS (SELECT 1 FROM view_materializations m"
            " WHERE m.tenant_id = app_tenant() AND m.account_id = r.account_id)"
            " AND NOT EXISTS (SELECT 1 FROM import_runs i WHERE i.tenant_id = app_tenant()"
            " AND i.account_id = r.account_id AND i.status IN ('pending', 'running')"
            " AND coalesce(i.started_at, i.created_at) > now() - make_interval(secs => :stale))"
        ),
        {"accts": list(accounts), "stale": STALE_RUNNING_S},
    )
    return tuple(sorted(UUID(str(r.account_id)) for r in found.all()))


async def native_tables(
    session: Any, accounts: Sequence[UUID], covered: Mapping[str, set[UUID]]
) -> list[TableInfo]:
    """The imported accounts' native rows (``imported_accounts``), one table per kind that has
    rows (``documents.repo.NATIVE_KINDS``); ``covered`` (kind -> the accounts a materialised
    table of the kind holds) only names the table. One statement per kind counts the rows and
    collects the routing values of its low-cardinality columns."""
    out: list[TableInfo] = []
    accts = await imported_accounts(session, accounts)
    if not accts:
        return out
    for kind in DOCREPO.NATIVE_KINDS:
        hidden = await _hidden(session, kind, accts)
        if hidden is None:
            continue
        view = ViewSource(kind, (), accts, source="native", hidden=hidden)
        source, bound = DOCREPO.rows_source(view)
        # the column keys are this module's literals, never a caller's text
        agg = ", ".join(
            f"array_agg(DISTINCT r.cells ->> '{key}') AS {key}" for key in _NATIVE_VALUES[kind]
        )
        row = (
            await session.execute(text(f"SELECT count(*) AS n, {agg} FROM {source}"), bound)  # nosec B608
        ).first()
        count = int(row.n) if row else 0
        if not count:
            continue
        multi = {key for key, _h, _t, _p, m in _NATIVE_COLUMNS[kind] if m}
        values = {key: _distinct(getattr(row, key), key in multi) for key in _NATIVE_VALUES[kind]}
        columns = (
            _column("account", "Account", "text_short", routed=False),
            _column("file_row", "File row", "count", routed=False),
            *(
                replace(_native_column(key, header, ctype, payload, m), values=values.get(key, ()))
                for key, header, ctype, payload, m in _NATIVE_COLUMNS[kind]
            ),
        )
        suffix = " (imported)" if kind in covered and covered[kind] else ""
        out.append(
            TableInfo(
                id=uuid5(_NAMESPACE, f"native:{kind}:{','.join(map(str, accts))}"),
                name=f"Research: {TITLES[kind]}{suffix}",
                table=f"Research:{TITLES[kind]}{suffix}",
                member=None,
                document=(
                    "the imported workspace rows"
                    f" ({len(accts)} of {len(accounts)} accounts; the account's current rows)"
                ),
                document_id=accts[0],
                account_id=accts[0],
                columns=columns,
                business_key=None,
                row_count=count,
                view=view,
            )
        )
    return out
