"""The research results are tables Ask queries whole (Ask redesign phase 2, 2026-11-02): the
delivered run's rows of every account in scope, with the same tools an uploaded spreadsheet gets.
SYNTHETIC data only.

GUARANTEE(test_research_results_are_offered_as_complete_tables)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import research_tables as RT
from documents import tables as T
from documents.tables import ColumnInfo

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
H = "b" * 64

PEOPLE = [
    {"name": "Ann", "title": "VP Engineering", "location": "San Francisco, CA, USA"},
    {"name": "Bob", "title": "Director", "location": "Oakland, California"},
    {"name": "Cy", "title": "Vice President, Data", "location": "Palo Alto, CA"},
    {"name": "Di", "title": "Analyst", "location": "Sacramento, CA"},
    {"name": "Eve", "title": "Manager", "location": "Remote"},
    {"name": "Fay", "title": "SVP Sales", "location": "Greater San Francisco Bay Area"},
    {"name": "Gil", "title": "Engineer", "location": "Los Angeles, CA"},
    {"name": "Hal", "title": "Engineer", "location": ""},
]
PEOPLE_2 = [{"name": "Zed", "title": "Architect", "location": "Berkeley, CA"}]
JOBS = [
    {"title": "Salesforce Developer", "status": "Open", "first_seen_text": "2026-03-10"},
    {"title": "Data Analyst", "status": "Closed", "first_seen_text": ""},
    {"title": "Salesforce Architect", "status": "Open", "first_seen_text": "2026-01-05"},
]


def _accounts(tenant: UUID) -> list[tuple[UUID, str]]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT id, coalesce(name, domain) FROM accounts WHERE tenant_id = %s"
            " ORDER BY domain LIMIT 2",
            (tenant,),
        ).fetchall()
    return [(UUID(str(a)), str(n)) for a, n in rows]


def _deliver(
    tenant: UUID, account: UUID, run: UUID, tables: dict[str, list[dict[str, str]]]
) -> None:
    """A delivered run with its materialised tables (what ``views.materialize`` leaves): one
    deliverable per kind, one materialisation, the rows of every table."""
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        for kind, rows in tables.items():
            h = hashlib.sha256(f"{kind}{H}".encode()).hexdigest()
            # a later version of the account's file (the seed may already hold the first)
            prev = c.execute(
                "SELECT id, version FROM deliverables WHERE tenant_id = %s AND account_id = %s"
                " AND kind = %s ORDER BY version DESC LIMIT 1",
                (tenant, account, kind),
            ).fetchone()
            c.execute(
                "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
                " supersedes, filename, sha256, size_bytes, object_key, manifest_json,"
                " content_hash, snapshot_hash, theme_hash, rules_hash, built_at, owner_user_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 'f.xlsx', %s, 10, %s, %s, %s, %s, %s, %s,"
                " now(), 'priya-test')",
                (
                    tenant,
                    account,
                    run,
                    H,
                    kind,
                    prev[1] + 1 if prev else 1,
                    prev[0] if prev else None,
                    h,
                    f"{tenant}/{account}/{run}/deliverables/{h}.xlsx",
                    json.dumps({"rows": len(rows)}),
                    h,
                    h,
                    h,
                    h,
                ),
            )
        c.execute(
            "INSERT INTO view_materializations (tenant_id, run_id, account_id, counts, rows_hash,"
            " status, created_at, exclusion_hash) VALUES (%s, %s, %s, %s, %s, 'ready', now(),"
            " 'none')",
            (
                tenant,
                run,
                account,
                json.dumps({k: len(v) for k, v in tables.items()}),
                hashlib.sha256(H.encode()).hexdigest(),
            ),
        )
        for kind, rows in tables.items():
            for n, cells in enumerate(rows, start=1):
                c.execute(
                    "INSERT INTO view_rows (tenant_id, run_id, account_id, table_kind, position,"
                    " ref, row_id, cells, source_state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
                    " 'no_source')",
                    (tenant, run, account, kind, n, f"{kind[0]}{n}", f"id{n}", json.dumps(cells)),
                )


@pytest.fixture(scope="module")
def delivered(world: dict[str, Any]) -> dict[str, Any]:
    tenant = world["a"]
    (a1, n1), (a2, n2) = _accounts(tenant)
    _deliver(tenant, a1, uuid4(), {"jobs": JOBS, "people": PEOPLE})
    _deliver(tenant, a2, uuid4(), {"jobs": JOBS[:1], "people": PEOPLE_2})
    return {"tenant": tenant, "accounts": [a1, a2], "names": [n1, n2]}


async def _tables(d: dict[str, Any]) -> list[T.TableInfo]:
    async with tenant_txn(d["tenant"]) as s:
        return await RT.research_tables(s, d["accounts"])


# an uploaded workspace, imported into the native tables under a file-import run (no
# deliverables row, no materialisation): the UCB shape of 2026-11-04
IMPORTED_PEOPLE = [
    ("Doris Lu", "Director, CRM", "director", "San Francisco Bay Area", "A", ["Salesforce"]),
    ("John Derasmo", "Senior Manager, Commercial IT", "other", "Fremont, California", "B", []),
    ("Werner Raeymaeckers", "Head of Digital Technology", "head", "Atlanta, Georgia", "A",
     ["Salesforce", "Veeva"]),
    ("Anurag Khare", "Data Engineer", "other", "Atlanta Metropolitan Area", "", []),
]  # fmt: skip
IMPORTED_JOBS = [
    ("id:94240", "Digital Technology Lead", "open", "Atlanta, GA", ["Salesforce", "Veeva"],
     "2026-09-30"),
    ("id:93642", "Technical Lead CRM", "closed", "Atlanta, GA", ["Salesforce"], "2026-05-01"),
    ("id:90001", "SAP Basis Lead", "open", "Brussels", ["SAP"], None),
]  # fmt: skip


def _import(tenant: UUID, account: UUID, run: UUID) -> list[str]:
    """The native rows an import writes (people, postings, a detection); the people's ids."""
    from tests.conftest import ADMIN_DSN

    ids: list[str] = []
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        # the file-import run the rows hang off (runs.lifecycle.record_file_import_run)
        c.execute(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, finished_at,"
            " finished_reason, graph_version, budget_json, reserved_credits, idempotency_key)"
            " VALUES (%s, %s, %s, 'COMPLETED', now(), 'file_import', 'file-import', '{}', 0, %s)",
            (run, tenant, account, f"file-import-{run}"),
        )
        for n, (name, title, level, loc, priority, lanes) in enumerate(IMPORTED_PEOPLE, 1):
            (pid,) = c.execute(
                "INSERT INTO people (tenant_id, account_id, run_id, profile_url, name, title,"
                " job_level, source_ref, excluded, person_key, location, lanes, priority,"
                " scope_status, scope_reason, origin) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                " 'import', false, %s, %s, %s, %s, %s, %s, 'title_search') RETURNING id",
                (tenant, account, run, f"https://www.linkedin.com/in/imported-{n}", name,
                 title, level, f"import:{n}", loc, lanes, priority or None,
                 "in_scope" if priority else "review",
                 None if priority else "no priority in the workbook"),
            ).fetchone()  # fmt: skip
            ids.append(str(pid))
        for key, title, status, loc, platforms, posted in IMPORTED_JOBS:
            c.execute(
                "INSERT INTO job_postings (tenant_id, account_id, run_id, posting_key, row_json,"
                " status, window_bucket, title, location, platforms, lanes, posted_at,"
                " first_seen, url) VALUES (%s, %s, %s, %s, '{}', %s, 'in_window', %s, %s, %s,"
                " %s, %s, %s, %s)",
                (tenant, account, run, key, status, title, loc, platforms,
                 [p.casefold() for p in platforms], posted, posted,
                 f"https://jobs.example/{key[3:]}"),
            )  # fmt: skip
        c.execute(
            "INSERT INTO tech_detections (tenant_id, account_id, run_id, technology,"
            " source_count, source_ref) VALUES (%s, %s, %s, 'Salesforce', 3, 'S1')",
            (tenant, account, run),
        )
    return ids


@pytest.fixture(scope="module")
def imported(world: dict[str, Any]) -> dict[str, Any]:
    from tests.conftest import _make_account

    tenant = world["a"]
    account = _make_account(tenant, f"imported-{uuid4().hex[:6]}.example")
    ids = _import(tenant, account, uuid4())
    return {"tenant": tenant, "account": account, "people_ids": ids}


async def test_an_imported_workspace_is_offered_as_research_tables(
    imported: dict[str, Any],
) -> None:
    """An account with imported rows and no delivered run gets the three kinds from the native
    tables, with the same tools: exact counts by status and place, the routing values, describe
    and read_rows (the UCB answers of 2026-11-04 needed exactly these)."""
    from account_intelligence.replica import router as R

    d = {"tenant": imported["tenant"], "accounts": [imported["account"]]}
    tables = await _tables(d)
    assert [t.name for t in tables] == [
        "Research: People",
        "Research: Job postings",
        "Research: Tech stack and teams",
    ]
    people, jobs, tech = tables
    assert people.view is not None and people.view.source == "native"
    assert people.row_count == 4 and jobs.row_count == 3 and tech.row_count == 1
    assert "imported workspace" in people.document
    # the routing lexicon: a status word or a platform name routes to the table
    status = jobs.names["status"]
    assert status.values == ("Closed", "Open")
    assert jobs.names["platforms"].multi_valued
    assert set(jobs.names["platforms"].values) == {"SAP", "Salesforce", "Veeva"}
    assert people.names["level"].values == ("director", "head", "other")
    routed = R.rule_route("how many open Salesforce postings are there?", tables)
    assert routed is not None and routed.route == "tabular", routed
    async with tenant_txn(d["tenant"]) as s:
        open_sf = T.parse(
            jobs,
            {
                "where": [
                    {"column": "status", "op": "eq", "value": "Open"},
                    {"column": "platforms", "op": "icontains", "value": "Salesforce"},
                ]
            },
        )
        got = await T.run(s, open_sf)
        assert got["row_count"] == 1
        (row,) = got["rows"]
        assert row["cells"]["requisition"] == "94240" and row["cells"]["posted"] == "2026-09-30"
        assert row["cells"]["account"] and row["cells"]["file_row"] == 1
        # a blank date is no date; the dated rows compare as dates
        since = T.parse(jobs, {"where": [{"column": "posted", "op": "gte", "value": "2026-06-01"}]})
        assert [r["cells"]["requisition"] for r in (await T.run(s, since))["rows"]] == ["94240"]
        bay = T.parse(
            people, {"where": [{"column": "location", "op": "icontains", "value": "Fremont"}]}
        )
        assert [r["cells"]["name"] for r in (await T.run(s, bay))["rows"]] == ["John Derasmo"]
        heads = T.parse(people, {"where": [{"column": "level", "op": "eq", "value": "head"}]})
        assert [r["cells"]["name"] for r in (await T.run(s, heads))["rows"]] == [
            "Werner Raeymaeckers"
        ]
        described = await T.describe(s, people)
        assert described["row_count"] == 4
        assert {c["name"] for c in described["columns"]} >= {"name", "location", "lanes"}
        read = await T.read(s, people, [1, 4], [])
        assert read["row_count"] == 2 and not read["not_found"]
        assert read["rows"][1]["cells"]["name"] == "Anurag Khare"
        assert read["rows"][1]["cells"]["location"] == "Atlanta Metropolitan Area"


async def test_imported_rows_beside_a_delivered_run_are_a_second_table(
    delivered: dict[str, Any], imported: dict[str, Any]
) -> None:
    """Scope mixing a delivered account and an imported one: the materialised table keeps its
    name, the imported rows are the kind's second table, and no account is in both."""
    d = {"tenant": imported["tenant"], "accounts": [delivered["accounts"][0], imported["account"]]}
    names = [t.name for t in await _tables(d)]
    assert names == [
        "Research: People",
        "Research: Job postings",
        "Research: People (imported)",
        "Research: Job postings (imported)",
        "Research: Tech stack and teams",
    ]
    by_name = {t.name: t for t in await _tables(d)}
    assert by_name["Research: People"].view.account_ids == (delivered["accounts"][0],)
    assert by_name["Research: People (imported)"].view.account_ids == (imported["account"],)
    assert by_name["Research: People (imported)"].row_count == 4


async def test_an_excluded_person_is_hidden_from_the_imported_people_table(
    imported: dict[str, Any],
) -> None:
    """The tenant's CURRENT exclusions apply to the imported rows as to every other surface
    (F1): an excluded person is neither counted nor listed, and comes back when the exclusion
    is lifted."""
    from datetime import UTC, datetime

    from tenancy import settings as tenant_settings

    tenant = imported["tenant"]

    async def exclude(people: list[str]) -> None:
        async with tenant_txn(tenant) as s:
            cur = await tenant_settings.current(s)
            data = cur.settings.model_dump(mode="json")
            data["exclusions"] = {"people": people, "companies": []}
            await tenant_settings.save(
                s,
                tenant_settings.parse(data),
                actor="test",
                expected_version=cur.version,
                now=datetime.now(UTC),
            )

    d = {"tenant": tenant, "accounts": [imported["account"]]}
    await exclude(["https://www.linkedin.com/in/imported-1"])
    try:
        people = next(t for t in await _tables(d) if t.view and t.view.kind == "people")
        assert people.row_count == 3 and len(people.view.hidden) == 1
        async with tenant_txn(tenant) as s:
            names = [r["cells"]["name"] for r in (await T.run(s, T.parse(people, {})))["rows"]]
        assert "Doris Lu" not in names and len(names) == 3
    finally:
        await exclude([])
    people = next(t for t in await _tables(d) if t.view and t.view.kind == "people")
    assert people.row_count == 4 and people.view.hidden == ()


async def test_research_results_are_offered_as_complete_tables(delivered: dict[str, Any]) -> None:
    tables = await _tables(delivered)
    assert [t.name for t in tables] == ["Research: People", "Research: Job postings"]
    jobs = tables[1]
    assert jobs.view is not None and jobs.row_count == 4  # both accounts' rows, one table
    names = {c.name for c in jobs.columns}
    assert {"account", "file_row", "title", "status", "first_seen_text"} <= names
    async with tenant_txn(delivered["tenant"]) as s:
        out = await T.run(s, T.parse(jobs, {"where": []}))
        assert out["row_count"] == 4
        # the account column splits the accounts; the file row is the delivered file's row number
        first = T.parse(
            jobs, {"where": [{"column": "account", "op": "eq", "value": delivered["names"][0]}]}
        )
        got = await T.run(s, first)
        assert got["row_count"] == 3
        assert [r["cells"]["file_row"] for r in got["rows"]] == [1, 2, 3]
        # a blank date is no date, not an error; gte keeps only the dated rows on or after it
        late = T.parse(
            jobs, {"where": [{"column": "first_seen_text", "op": "gte", "value": "2026-03-01"}]}
        )
        found = await T.run(s, late)
        assert sorted(r["cells"]["title"] for r in found["rows"]) == [
            "Salesforce Developer",
            "Salesforce Developer",
        ]
        # describe and read_rows work on a research table as on an upload
        described = await T.describe(s, jobs)
        assert described["row_count"] == 4
        ids = [r["row"] for r in out["rows"]][:2]
        read = await T.read(s, jobs, ids, [])
        assert read["row_count"] == 2 and not read["not_found"]


async def test_a_run_built_under_another_exclusion_list_is_not_offered(
    delivered: dict[str, Any],
) -> None:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE view_materializations SET exclusion_hash = repeat('f', 16)"
            " WHERE tenant_id = %s",
            (delivered["tenant"],),
        )
    try:
        assert await _tables(delivered) == []
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(
                "UPDATE view_materializations SET exclusion_hash = 'none' WHERE tenant_id = %s",
                (delivered["tenant"],),
            )


async def test_the_read_plan_describes_and_reads_a_research_table_whole(
    delivered: dict[str, Any],
) -> None:
    from account_intelligence.replica import read_plan as RP

    tables = [t for t in await _tables(delivered) if t.name.endswith("Job postings")]
    async with tenant_txn(delivered["tenant"]) as s:
        plan = await RP.build(s, "list the job postings by title", tables, max_chars=100_000)
        assert [t.name for t in plan.tables] == ["Research: Job postings"]
        assert plan.described[0]["row_count"] == 4 and "<pre_read>" in plan.pre_read()
        whole = plan.whole[tables[0].id]
        # every row of both accounts, every cell, the account column included
        assert (
            whole.count("\n") == 4 and "Salesforce Architect" in whole and "Data Analyst" in whole
        )
        assert delivered["names"][1] in whole
        # a budget the rows exceed keeps the description and drops the whole text
        small = await RP.build(s, "list the job postings by title", tables, max_chars=10)
        assert small.described and small.whole == {}
        off = await RP.build(s, "list the job postings by title", tables, max_chars=0)
        assert off.whole == {}


async def _ask_tools(d: dict[str, Any]) -> Any:
    from account_intelligence.replica import tools as TL

    tables = await _tables(d)
    return TL.AccessTools(lambda: tenant_txn(d["tenant"]), tables, d["accounts"], None), tables


async def test_a_place_filter_also_returns_the_rows_nearby_nearest_first(
    delivered: dict[str, Any],
) -> None:
    tools, _ = await _ask_tools(delivered)
    await tools.run("describe_table", {"table": "Research: People"})
    out = await tools.run(
        "query_table",
        {
            "table": "Research: People",
            "where": [{"column": "location", "op": "icontains", "value": "San Francisco"}],
        },
    )
    # the exact matches stay their own set: the text holds the words
    assert sorted(r["cells"]["name"] for r in out["rows"]) == ["Ann", "Fay"]
    related = out["related"]
    assert related["target"] == "San Francisco, CA, US" and related["radius_miles"] == 50
    # Oakland (about 8 miles), Berkeley (another account's row, about 10), Palo Alto: nearest
    # first; not Sacramento (about 75 miles) or Los Angeles
    assert [r["cells"]["name"] for r in related["rows"]] == ["Bob", "Zed", "Cy"]
    assert related["rows"][0]["distance_miles"] < related["rows"][1]["distance_miles"] < 30
    assert related["unplaced"] == 2  # "Remote" and the blank
    # a wider radius reaches Sacramento; expand=false turns it all off
    wide = await tools.run(
        "query_table",
        {
            "table": "Research: People",
            "radius_miles": 100,
            "where": [{"column": "location", "op": "icontains", "value": "San Francisco"}],
        },
    )
    assert [r["cells"]["name"] for r in wide["related"]["rows"]][-1] == "Di"
    plain = await tools.run(
        "query_table",
        {
            "table": "Research: People",
            "expand": False,
            "where": [{"column": "location", "op": "icontains", "value": "San Francisco"}],
        },
    )
    assert "related" not in plain


async def test_a_title_filter_also_matches_the_spellings_of_its_alias_group(
    delivered: dict[str, Any],
) -> None:
    tools, _ = await _ask_tools(delivered)
    await tools.run("describe_table", {"table": "Research: People"})
    out = await tools.run(
        "query_table",
        {
            "table": "Research: People",
            "where": [{"column": "title", "op": "icontains", "value": "vice president"}],
        },
    )
    # "VP Engineering" and "Vice President, Data" match; "SVP Sales" does not (its own group)
    assert sorted(r["cells"]["name"] for r in out["rows"]) == ["Ann", "Cy"]
    assert out["expanded"] == [
        {"column": "title", "term": "vice president", "also": ["vp", "v.p."]}
    ]
    exact = await tools.run(
        "query_table",
        {
            "table": "Research: People",
            "expand": False,
            "where": [{"column": "title", "op": "icontains", "value": "vice president"}],
        },
    )
    assert [r["cells"]["name"] for r in exact["rows"]] == ["Cy"]


async def test_result_sections_become_complete_tables_with_notes_and_nearby_rows(
    delivered: dict[str, Any],
) -> None:
    from account_intelligence.replica import model_answer as MA
    from account_intelligence.replica import render as RENDER

    tools, tables = await _ask_tools(delivered)
    people = tables[0]
    await tools.run("describe_table", {"table": people.name})
    out = await tools.run(
        "query_table",
        {
            "table": people.name,
            "where": [{"column": "location", "op": "icontains", "value": "San Francisco"}],
        },
    )
    answer = {
        "headline": "Two people live in San Francisco; three more are nearby",
        "sections": [
            {
                "kind": "result",
                "result_id": out["result_id"],
                "columns": ["name", "title", "location"],
            }
        ],
    }
    done = await tools.run("render_answer", answer)
    assert done["terminal"] is True
    built = done["answer"]["sections"]
    exact, near = built
    headers = [people.names[c].raw for c in ("name", "title", "location")]
    assert exact["columns"] == ["Row", *headers, "Source"]
    assert [r[1] for r in exact["rows"]] == ["Ann", "Fay"]
    assert all(r[-1] == f"[T:{people.id}]" for r in exact["rows"])
    assert (
        "2 of 9 rows in Research: People match (location icontains San Francisco)" in exact["note"]
    )
    assert near["title"] == "Nearby: within 50 miles of San Francisco, CA, US"
    assert near["columns"][-3:] == ["Distance (mi)", "Read as", "Source"]
    assert [r[1] for r in near["rows"]] == ["Bob", "Zed", "Cy"]
    assert "2 other rows have no location that could be placed" in near["note"]
    # a result the model never ran, or a column the result lacks, is an error it can fix
    assert (
        await tools.run(
            "render_answer", {**answer, "sections": [{"kind": "result", "result_id": "q9"}]}
        )
    )["error"]["code"] == "unknown_result"
    assert (
        await tools.run(
            "render_answer",
            {
                **answer,
                "sections": [
                    {"kind": "result", "result_id": out["result_id"], "columns": ["salary"]}
                ],
            },
        )
    )["error"]["code"] == "bad_columns"
    # the placeholder becomes the numbered row of the table, so the citation check passes
    from account_intelligence.replica import answers as A

    acct = A.Acct(
        id=people.account_id,
        domain="x.example",
        name="X",
        lanes=[],
        cadence=None,
        last_refresh=None,
        refreshes=1,
    )
    row = A.Row(
        f"table:{people.id}",
        "file",
        acct,
        "t",
        "t",
        "m",
        None,
        None,
        "internal",
        extra={"table": people.name},
    )
    cited = MA._table_cites(done["answer"], {"R1": row})
    assert cited["sections"][0]["rows"][0][-1] == "[R1]"
    assert RENDER.rewrite(cited, {"R1": row}) is not None


def _rows(*cells: dict[str, Any]) -> list[Any]:
    from types import SimpleNamespace

    return [SimpleNamespace(row_no=i, seq=0, cells=c) for i, c in enumerate(cells, start=1)]


async def test_a_duplicate_the_dedupe_removed_never_comes_back_as_a_nearby_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-11-02: with ``distinct_on``, the rows ``_dedupe`` dropped from the exact result
    were not in the exclusion set, so they returned as "nearby" at 0 miles."""
    cols = tuple(ColumnInfo(n, n, "text", False) for n in ("req", "location", "last_seen"))
    table = T.TableInfo(uuid4(), "Jobs", "b:Jobs", None, "b.xlsx", uuid4(), uuid4(), cols, "req", 5)
    data = _rows(
        {"req": "R1", "location": "San Francisco, CA", "last_seen": "2026-10-01"},  # superseded
        {"req": "R1", "location": "San Francisco, CA", "last_seen": "2026-10-20"},
        {"req": "R2", "location": "Oakland, CA", "last_seen": "2026-10-01"},  # stale scrape
        {"req": "R2", "location": "San Francisco, CA", "last_seen": "2026-10-20"},
        {"req": "R3", "location": "San Jose, CA", "last_seen": "2026-10-05"},
    )

    async def fake(
        session: Any, table_id: Any, where: str, params: dict[str, Any], limit: int, **kw: Any
    ) -> list[Any]:
        if "v0" in params:  # the exact query: the place filter
            return [r for r in data if "san francisco" in r.cells["location"].lower()]
        return list(data)  # the relaxed query

    monkeypatch.setattr(T.repo, "table_rows", fake)
    query = T.parse(
        table,
        {
            "where": [{"column": "location", "op": "icontains", "value": "San Francisco"}],
            "distinct_on": "req",
        },
    )
    out = await T.run(None, query)
    assert sorted(r["cells"]["req"] for r in out["rows"]) == ["R1", "R2"]
    assert out["dedup_removed"] == 1
    related = out["related"]["rows"]
    # R1 and R2 are already listed (their current rows); only R3, San Jose, is nearby
    assert [r["cells"]["req"] for r in related] == ["R3"]


async def test_a_place_filter_on_a_value_that_is_no_city_costs_no_second_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cols = (ColumnInfo("location", "Location", "text", False),)
    table = T.TableInfo(uuid4(), "P", "b:P", None, "b.xlsx", uuid4(), uuid4(), cols, None, 2)
    calls: list[str] = []

    async def fake(
        session: Any, table_id: Any, where: str, params: dict[str, Any], limit: int, **kw: Any
    ) -> list[Any]:
        calls.append(where)
        return _rows({"location": "Remote"})

    monkeypatch.setattr(T.repo, "table_rows", fake)
    out = await T.run(
        None,
        T.parse(table, {"where": [{"column": "location", "op": "icontains", "value": "Remote"}]}),
    )
    assert out["related"] == {"skipped": "target_is_not_a_city", "column": "location"}
    assert len(calls) == 1  # the filter's own query only


def test_a_code_built_table_marks_partial_rows_lists_omitted_columns_and_honours_exclusions() -> (
    None
):
    from types import SimpleNamespace

    from account_intelligence.replica import result_tables as RTB

    names = [f"col{i}" for i in range(14)]
    table = SimpleNamespace(
        id="tid",
        name="Jobs",
        row_count=50,
        columns=[SimpleNamespace(name=n, raw=n.upper(), payload=False) for n in names],
    )
    table.names = {c.name: c for c in table.columns}
    rows = [
        {
            "row": i,
            "cells": {n: f"v{i} [2]" if n == "col0" else i % 2 == 0 for n in names},
            **({"partial": True} if i == 2 else {}),
        }
        for i in (1, 2, 3, 4)
    ]
    out = {"rows": rows, "row_count": 4, "applied_where": [], "partial_rows": 1}
    section = {"kind": "result", "result_id": "q1", "exclude_rows": [4]}
    built, error = RTB.build(section, {"q1": (table, SimpleNamespace(columns=[]), out)})
    assert error is None
    main = built[0]
    # ten columns plus Row and Source; the four left out are named, never dropped silently
    assert (
        len(main["columns"]) == 12
        and "COL13" in main["note"]
        and "4 of the result's columns" in main["note"]
    )
    # the partial row is marked and counted; row 4 is left out at the answer's request
    assert [r[0] for r in main["rows"]] == ["1", "2 (partial)", "3"]
    assert "1 rows matched only as part of a longer value" in main["note"]
    assert "Left out at the answer's request: 1 rows" in main["note"]
    # booleans read Yes/No and a bracketed number is not a citation chip
    assert main["rows"][0][1] == "v1 (2)" and main["rows"][0][2] == "No"
    # a result with no rows is a valid empty table
    empty, err = RTB.build(
        {"kind": "result", "result_id": "q1"},
        {
            "q1": (
                table,
                SimpleNamespace(columns=[]),
                {"rows": [], "row_count": 0, "applied_where": []},
            )
        },
    )
    assert err is None and empty[0]["rows"] == []


async def test_an_import_still_writing_offers_nothing(imported: dict[str, Any]) -> None:
    """While the account's import is pending or running (``documents.import_status``), its rows
    are not offered: a count over half-written rows would be wrong. A run left running past the
    stale window (its worker died) does not hold the rows back."""
    from tests.conftest import ADMIN_DSN

    tenant, account = imported["tenant"], imported["account"]
    d = {"tenant": tenant, "accounts": [account]}
    run_id = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "INSERT INTO import_runs (id, tenant_id, owner_user_id, account_id, document_id,"
            " status, started_at) VALUES (%s, %s, 'test-owner', %s, %s, 'running', now())",
            (run_id, tenant, account, uuid4()),
        )
    try:
        assert await _tables(d) == []
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute("SET session_replication_role = replica")
            c.execute(
                "UPDATE import_runs SET started_at = now() - interval '3 hours' WHERE id = %s",
                (run_id,),
            )
        assert [t.view.kind for t in await _tables(d) if t.view] == ["people", "jobs", "tech"]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute("SET session_replication_role = replica")
            c.execute("UPDATE import_runs SET status = 'ok' WHERE id = %s", (run_id,))
        assert len(await _tables(d)) == 3
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute("SET session_replication_role = replica")
            c.execute("DELETE FROM import_runs WHERE id = %s", (run_id,))


async def test_imported_tables_are_isolated_between_tenants(
    world: dict[str, Any], imported: dict[str, Any]
) -> None:
    """Client B's imported rows never reach client A: with row level security on, and with it
    off (a superuser) under A's bound tenant, since every statement carries ``app_tenant()`` as
    its own predicate (upload hardening PR1)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from account_intelligence.db.session import bind_tenant
    from tests.conftest import ADMIN_DSN, _make_account

    tenant_a, tenant_b = imported["tenant"], world["b"]
    account_b = _make_account(tenant_b, f"imported-b-{uuid4().hex[:6]}.example")
    _import(tenant_b, account_b, uuid4())
    # B sees its own rows; A, asking for B's account, sees nothing (RLS on)
    async with tenant_txn(tenant_b) as s:
        own = await RT.research_tables(s, [account_b])
    assert [t.row_count for t in own] == [4, 3, 1]
    async with tenant_txn(tenant_a) as s:
        assert await RT.research_tables(s, [account_b]) == []
        mixed = await RT.research_tables(s, [imported["account"], account_b])
        assert [(t.row_count, t.view.account_ids) for t in mixed if t.view] == [
            (4, (imported["account"],)),
            (3, (imported["account"],)),
            (1, (imported["account"],)),
        ]
    # RLS off: a superuser bound to A still reads none of B's rows, even through a forged source
    engine = create_async_engine(ADMIN_DSN.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn, conn.begin():
            s = AsyncSession(bind=conn)
            await bind_tenant(s, tenant_a, user="test-owner", system=True)
            assert await s.scalar(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
            assert await RT.research_tables(s, [account_b]) == []
            forged = next(t for t in own if t.view and t.view.kind == "people")
            got = await T.run(s, T.parse(forged, {"where": []}))
            assert got["row_count"] == 0 and got["rows"] == []
            assert await T.read(s, forged, [1, 2], []) == {
                **(await T.read(s, forged, [1, 2], [])),
                "row_count": 0,
            }
            mine = await RT.research_tables(s, [imported["account"]])
            assert [t.row_count for t in mine] == [4, 3, 1]
            await s.close()
    finally:
        await engine.dispose()
