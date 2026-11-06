"""The Job postings sheet of an imported workspace, narrowed by a match (an Ask citation's link,
owner 2026-11-04): only the rows whose Location contains the text, the total and every chip
count within the match, and the CSV export holding the same rows. SYNTHETIC workspace zip
(``tests.documents.workspace_zip``) through the production import path."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import sheets as SH
from account_intelligence.replica import table_export as TX
from tests.conftest import _make_account
from tests.documents import workspace_zip
from tests.documents.test_workspace_zip_import_db import _zip_with_twins
from tests.documents.world import service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

OWNER = "test-owner"  # the account's owner (tests.conftest._make_account)
CITY = "brentford"  # one of the fixture's posting locations ("Brentford, UK"), typed in lower case


async def test_the_jobs_sheet_with_a_location_match_reads_and_exports_only_those_rows(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
    assert await svc.process(tenant_a, UUID(done["document_id"])) == "indexed"
    now = datetime.now(UTC)
    whole = SH.parse("jobs", None, None, None, 0, SH.MAX_PAGE)
    matched = SH.parse(
        "jobs", None, None, None, 0, SH.MAX_PAGE, match_col="location", match=CITY.title()
    )
    async with tenant_txn(tenant_a, user=OWNER) as s:
        everything = await SH.read(s, whole, account, OWNER, now)
        every_row = [r async for r in TX.rows(s, whole, account, OWNER, now)]
        page = await SH.read(s, matched, account, OWNER, now)
        exported = [r async for r in TX.rows(s, matched, account, OWNER, now)]
        csv_text = b"".join(
            [c async for c in TX.csv_chunks(matched, TX.rows(s, matched, account, OWNER, now))]
        ).decode("utf-8-sig")

    assert len(every_row) == everything["total"]
    expected = [r for r in every_row if CITY in r["cells"]["location"].lower()]
    assert 0 < len(expected) < everything["total"]  # the match narrows, and something is left
    assert page["match"] == {"col": "location", "label": "Location", "value": "Brentford"}
    assert page["total"] == len(page["rows"]) == len(expected)
    assert all(CITY in r["cells"]["location"].lower() for r in page["rows"])
    assert sorted(r["cells"]["title"] for r in page["rows"]) == sorted(
        r["cells"]["title"] for r in expected
    )
    # every chip counts within the match, as the same chip over the whole sheet's matching rows
    chips = {f["key"]: f["count"] for f in page["filters"]}
    assert chips["all"] == page["total"]
    assert chips["open"] == sum(1 for r in expected if r["cells"]["status"] == "Open")
    assert all(chips[f["key"]] <= f["count"] for f in everything["filters"])
    # the export reads the same rows, in the same order, and the CSV holds them
    assert [r["cells"] for r in exported] == [r["cells"] for r in page["rows"]]
    lines = list(csv.reader(io.StringIO(csv_text)))
    assert lines[0][:3] == ["#", "Role", "Lane"]
    assert len(lines) - 1 == len(expected)
    assert [line[1] for line in lines[1:]] == [r["cells"]["title"] for r in page["rows"]]

    # a value no row holds reads empty, with every count at zero; LIKE wildcards are literal
    for nothing in ("%", "_", "no such city"):
        q = SH.parse("jobs", None, None, None, match_col="location", match=nothing)
        async with tenant_txn(tenant_a, user=OWNER) as s:
            empty = await SH.read(s, q, account, OWNER, now)
        assert empty["total"] == 0 and not empty["rows"], nothing
        assert not any(f["count"] for f in empty["filters"]), nothing
