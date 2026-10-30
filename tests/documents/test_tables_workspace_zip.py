"""Spreadsheet parity on a whole account workspace zip: the SYNTHETIC ``Holdings_workspace.zip``
(``tests.documents.workspace_zip``, the structure of a delivered workspace package) through the
production path: the zip's members read, the Jobs sheet held as rows, and the golden question
answered from the store with the sheet's own numbers: 40 postings name Salesforce, all Closed;
the strict question (open and first seen on or after 2026-03-01) is 0 rows; relaxing the status
alone gives 20 rows, 14 unique postings after the requisition dedupe (twelve keyed groups and
the two keyless pairs merging on title plus location). The zip is built in memory, so there is
nothing to be missing and nothing real in the repository (owner decision 2026-10-30; the former
real-account fixture is parked in ``unwanted-things/real-account-fixtures/``).

GUARANTEE(test_the_synthetic_workspace_zip_jobs_sheet_queries_to_the_known_truth)
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
from tests.documents import workspace_zip
from tests.documents.world import doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

STRICT = [
    {"column": "platforms_named", "op": "icontains", "value": "salesforce"},
    {"column": "status", "op": "eq", "value": "Open"},
    {"column": "first_seen", "op": "gte", "value": "2026-03-01"},
]
# platform owners and admins among the Salesforce postings, and the field-sales false
# positives (their text speaks of the people, never the platform: "our salesforce")
OWNER_IDS = workspace_zip.OWNER_IDS
FALSE_POSITIVES = workspace_zip.FALSE_POSITIVES
FIELD_SALES = re.compile(r"(?:the|our) salesforce\b")


async def _run(tenant: UUID, table: T.TableInfo, **payload: Any) -> dict[str, Any]:
    async with tenant_txn(tenant, user="user-1") as s:
        return await T.run(s, T.parse(table, payload))


async def test_the_zip_is_synthetic_and_stable() -> None:
    """Same bytes on every call; the members are the delivered package's shape; the names are
    the fictional account's."""
    import io
    import zipfile

    data = workspace_zip.workspace_zip_bytes()
    assert data == workspace_zip.workspace_zip_bytes()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert f"holdings/deliverables/{workspace_zip.JOBS_FILE}" in names
    assert "holdings/RESTORE.md" in names and any(n.startswith("holdings/data/") for n in names)
    assert workspace_zip.ACCOUNT_DOMAIN.endswith(".example")


async def test_the_synthetic_workspace_zip_jobs_sheet_queries_to_the_known_truth(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(
        svc, tenant_a, workspace_zip.ZIP_NAME, workspace_zip.workspace_zip_bytes(), account
    )
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    assert doc_row(doc)["status"] == "indexed"

    async with tenant_txn(tenant_a, user="user-1") as s:
        found = await T.list_tables(s, [account])
    jobs = next(t for t in found if t.table.endswith(workspace_zip.JOBS_TABLE))
    # the workspace's other workbooks are tables of their own, and the Teams sheet is the
    # 40-row decoy the questions are not about
    names = {t.table.rsplit("/", 1)[-1] for t in found}
    assert {
        workspace_zip.JOBS_TABLE,
        f"{workspace_zip.PEOPLE_FILE}:People",
        f"{workspace_zip.TECH_FILE}:Teams",
    } <= names

    # the sheet's shape: 644 rows under 13 typed columns, the requisition the business key,
    # the long description payload; the row passage holds the short columns
    assert jobs.row_count == 644 and jobs.business_key == "requisition" and len(jobs.columns) == 13
    types = {c.name: c.type for c in jobs.columns}
    assert types["requisition"] == "number" and types["status"] == "text"
    assert types["first_seen"] == "date" and types["last_seen"] == "date"
    assert [c.name for c in jobs.columns if c.payload] == ["full_description"]

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

    # relaxing exactly the status: 20 rows, 14 unique postings
    relaxed = STRICT[::2]
    full = await _run(tenant_a, jobs, where=relaxed, limit=100)
    assert full["row_count"] == workspace_zip.RELAXED_ROWS and len(full["rows"]) == 20
    assert all(r["cells"]["status"] == "Closed" for r in full["rows"])
    unique = await _run(tenant_a, jobs, where=relaxed, distinct_on="requisition", limit=100)
    assert unique["row_count"] == workspace_zip.RELAXED_UNIQUE
    assert unique["dedup_removed"] == workspace_zip.RELAXED_ROWS - workspace_zip.RELAXED_UNIQUE
    reqs = {r["cells"]["requisition"] for r in unique["rows"]}
    assert reqs >= OWNER_IDS and reqs >= FALSE_POSITIVES
    # the two keyless postings (two rows each) merge on title plus location: one row each
    assert sum(r["cells"]["requisition"] is None for r in unique["rows"]) == 2
    first_owner = next(r for r in unique["rows"] if r["cells"]["requisition"] == 546999)
    # a requisition on two rows survives once, the row seen last
    twice = await _run(
        tenant_a, jobs, where=[{"column": "requisition", "op": "eq", "value": 546999}]
    )
    assert twice["row_count"] == 2
    seen = {r["row"]: r["cells"]["last_seen"] for r in twice["rows"]}
    assert first_owner["row"] == max(seen, key=lambda row: seen[row])

    # the full 40-row Salesforce set, every row Closed, complete (never a sample)
    forty = await _run(tenant_a, jobs, where=STRICT[:1], limit=100)
    assert forty["row_count"] == workspace_zip.SALESFORCE_ROWS and not forty["truncated"]
    assert {r["cells"]["status"] for r in forty["rows"]} == {"Closed"}
    assert "full_description" not in forty["rows"][0]["cells"]  # payload only on request

    # keyless postings (the four rows of the two keyless pairs)
    nulls = await _run(tenant_a, jobs, where=[{"column": "requisition", "op": "is_null"}])
    assert nulls["row_count"] == 4

    # read_rows: whole rows, payload intact, and the false positives speak of the salespeople
    row_nos = {
        r["cells"]["requisition"]: r["row"] for r in forty["rows"] if r["cells"]["requisition"]
    }
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
