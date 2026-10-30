"""GAP 1, spreadsheet parity on the owner's REAL GSK workspace (2026-10-17): the actual
``GSK_workspace.zip`` (``tests/fixtures/gsk/``) through the production path — the zip's
members read, the Jobs sheet held as rows — and the golden question answered from the store
with the sheet's own numbers: 40 postings name Salesforce, all Closed; the strict question
(open and first seen on or after 2026-03-01) is 0 rows; relaxing the status alone gives 21
rows, 19 unique postings after the requisition dedupe — twenty keyed groups, the re-scraped
keyless Espoo row merging onto its Finland requisition, and the 437026 pair keeping the row
seen last. The fixture must be present: the test FAILS when it is missing, never skips.

GUARANTEE(test_the_real_gsk_jobs_sheet_queries_to_the_known_truth)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents import tables as T
from tests.conftest import _make_account
from tests.documents.world import doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

ZIP = Path(__file__).resolve().parent.parent / "fixtures" / "gsk" / "GSK_workspace.zip"

STRICT = [
    {"column": "platforms_named", "op": "icontains", "value": "salesforce"},
    {"column": "status", "op": "eq", "value": "Open"},
    {"column": "first_seen", "op": "gte", "value": "2026-03-01"},
]
# platform owners and admins among the Salesforce postings, and the field-sales false
# positives (their text speaks of the people, never the platform: "…the salesforce")
OWNER_IDS = {442977, 442570, 440446, 439962, 436657}
FALSE_POSITIVES = {443372, 443075, 439031, 436438}
FIELD_SALES = re.compile(r"(?:the|our) salesforce\b")


async def _run(tenant: UUID, table: T.TableInfo, **payload: Any) -> dict[str, Any]:
    async with tenant_txn(tenant, user="user-1") as s:
        return await T.run(s, T.parse(table, payload))


async def test_the_real_gsk_jobs_sheet_queries_to_the_known_truth(
    tenant_a: UUID, tmp_path: Path
) -> None:
    if not ZIP.exists():
        pytest.fail(f"the real GSK workspace fixture is missing: {ZIP}")
    account = _make_account(tenant_a, f"gsk-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, ZIP.name, ZIP.read_bytes(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    assert doc_row(doc)["status"] == "indexed"

    async with tenant_txn(tenant_a, user="user-1") as s:
        found = await T.list_tables(s, [account])
    jobs = next(t for t in found if t.table.endswith("GSK_Job_Postings.xlsx:Jobs"))

    # the sheet's shape: 644 rows under 13 typed columns, the requisition the business key,
    # the long description payload; the row passage holds the short columns
    assert jobs.row_count == 644 and jobs.business_key == "requisition" and len(jobs.columns) == 13
    types = {c.name: c.type for c in jobs.columns}
    assert types["requisition"] == "number" and types["status"] == "text"
    assert types["first_seen"] == "date" and types["last_seen"] == "date"
    assert [c.name for c in jobs.columns if c.payload] == ["full_description"]
    multi = {c.name: c.multi_valued for c in jobs.columns}
    # the real sheet: titles and locations are the list-y columns; only a ninth of the
    # "Platforms named" cells are lists, under the threshold
    assert multi["platforms_named"] is False and multi["location"] is True

    # the router's lexicon rides on the columns (Gap 4): natural forms and the low-cardinality
    # values of each such column
    cols = {c.name: c for c in jobs.columns}
    assert {"first seen", "first seen date"} <= set(cols["first_seen"].aliases)
    assert set(cols["status"].values) == {"Open", "Closed"}
    assert "Salesforce" in cols["platforms_named"].values
    assert "Platform owner" in cols["class"].values

    # the golden question: no Salesforce posting is open and first seen on or after 1 March
    strict = await _run(tenant_a, jobs, where=STRICT, distinct_on="requisition")
    assert strict["row_count"] == 0 and strict["rows"] == []

    # relaxing exactly the status: 21 rows, 19 unique postings
    relaxed = STRICT[::2]
    full = await _run(tenant_a, jobs, where=relaxed, limit=100)
    assert full["row_count"] == 21 and len(full["rows"]) == 21
    assert all(r["cells"]["status"] == "Closed" for r in full["rows"])
    unique = await _run(tenant_a, jobs, where=relaxed, distinct_on="requisition", limit=100)
    assert unique["row_count"] == 19 and unique["dedup_removed"] == 2
    reqs = {r["cells"]["requisition"] for r in unique["rows"]}
    assert reqs >= OWNER_IDS and reqs >= FALSE_POSITIVES and None not in reqs
    # the re-scraped keyless Espoo row (sheet row 20) merges onto the Finland posting it is a
    # re-scrape of; the survivor is the requisition seen last
    finland = next(r for r in unique["rows"] if r["cells"]["requisition"] == 446999)
    assert finland["row"] == 11 and finland["cells"]["last_seen"] == "2026-09-15"
    assert finland["cells"]["location"].startswith("Finland")
    assert 20 not in {r["row"] for r in unique["rows"]}
    twice = await _run(
        tenant_a, jobs, where=[{"column": "requisition", "op": "eq", "value": 437026}]
    )
    assert twice["row_count"] == 2
    kept = next(r for r in unique["rows"] if r["cells"]["requisition"] == 437026)
    assert kept["row"] == 317  # the one seen last (2026-06-15)

    # the full 40-row Salesforce set, every row Closed, complete (never a sample)
    forty = await _run(tenant_a, jobs, where=STRICT[:1], limit=100)
    assert forty["row_count"] == 40 and not forty["truncated"]
    assert {r["cells"]["status"] for r in forty["rows"]} == {"Closed"}
    assert "full_description" not in forty["rows"][0]["cells"]  # payload only on request

    # a keyless posting (54 rows) and a pre-cutoff duplicate requisition (434567 twice)
    nulls = await _run(tenant_a, jobs, where=[{"column": "requisition", "op": "is_null"}])
    assert nulls["row_count"] == 54
    pair = await _run(
        tenant_a, jobs, where=[{"column": "requisition", "op": "eq", "value": 434567}]
    )
    assert pair["row_count"] == 2

    # read_rows: whole rows, payload intact, and the false positives speak of the salespeople
    row_nos = {r["cells"]["requisition"]: r["row"] for r in forty["rows"]}
    async with tenant_txn(tenant_a, user="user-1") as s:
        nos, columns = T.parse_read(
            jobs,
            {
                "table": jobs.name,
                "rows": sorted(row_nos[r] for r in FALSE_POSITIVES),
                "columns": ["title", "platform_sentences"],
            },
        )
        fp = await T.read(s, jobs, nos, columns)
        nos, columns = T.parse_read(
            jobs, {"table": jobs.name, "rows": [2], "columns": ["full_description"]}
        )
        whole = await T.read(s, jobs, nos, columns)
    assert [r["row"] for r in fp["rows"]] == sorted(row_nos[r] for r in FALSE_POSITIVES)
    assert all(FIELD_SALES.search(r["cells"]["platform_sentences"]) for r in fp["rows"])
    assert len(whole["rows"][0]["cells"]["full_description"]) > 5000
