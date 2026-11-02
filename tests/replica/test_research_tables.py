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
