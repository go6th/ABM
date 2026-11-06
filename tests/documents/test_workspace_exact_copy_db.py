"""An uploaded workspace comes back word for word (owner 2026-11-05). SYNTHETIC data only: the
owner-shaped package of ``workspace_zip_owner``.

The UCB upload of 2026-11-04 came back changed: the import folded people with one name and two
profile links, folded postings the sheet listed twice, dropped the archive records without a
link, let the Standard redaction rewrite names and profile links ("Pankaj Kalambe" became
"Pan[NATIONAL_ID_7]"), registered none of the five files as the account's, and Download
everything rebuilt every table through the repair stage with other columns, other words and
generated rows. Each of those is one assertion here:

* the table store holds every sheet of every workbook and every table of every Word file, every
  row and every cell as the package has it (``documents.exact_copy.report``);
* the five files are the account's delivered files, byte for byte, shown by the screens as they
  are (``replica.workspace.deliverables``, ``replica.file_tabs``), never materialised;
* the native people and postings are one row per sheet row, nothing folded, the linkless
  archive record included;
* Download everything ships the files and their own sheets as CSV and Excel, an import report,
  and no repair report.

GUARANTEE(test_an_uploaded_workspace_comes_back_word_for_word)
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from documents import exact_copy as EC
from tests.conftest import _make_account
from tests.documents import workspace_zip_owner as OWNER
from tests.documents.world import admin, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


class _Writer:
    """The archive writer's interface, recording members in memory."""

    def __init__(self) -> None:
        self.members: dict[str, bytes] = {}

    @property
    def names(self) -> list[str]:
        return list(self.members)

    async def add_bytes(self, arcname: str, data: bytes) -> None:
        self.members[arcname] = data

    async def add_stream(self, arcname: str, reader: Any) -> None:
        self.members[arcname] = reader.read()


def _package_members(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {
            n.rsplit("/", 1)[-1]: z.read(n)
            for n in z.namelist()
            if "/deliverables/" in n and not n.endswith("/")
        }


async def test_an_uploaded_workspace_comes_back_word_for_word(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from account_intelligence.replica import downloads as D
    from account_intelligence.replica import file_tabs as FT
    from account_intelligence.replica import sheets as SH
    from account_intelligence.replica import workspace as W

    package = OWNER.workspace_zip_bytes()
    account = _make_account(tenant_a, OWNER.WORKSPACE_DOMAIN)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, OWNER.ZIP_NAME, package, account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "indexed"

    # 1. the store, the files and the native rows: no difference at all
    report = await EC.report(tenant_a, account, document, package, payloads=svc.payloads)
    assert not report.differences, EC.render(report)
    text = EC.render(report)
    assert "SAME" in text and f"{OWNER.PEOPLE_FILE} / People" in text

    # 2. the five files are the account's delivered files, byte for byte, and the screens open
    # them as they are
    files = _package_members(package)
    (run_id,) = admin(
        "SELECT id FROM account_runs WHERE account_id = %s AND finished_reason = 'file_import'",
        account,
    )[0]
    stored = admin(
        "SELECT kind, filename, sha256, manifest_json->>'source' FROM deliverables"
        " WHERE account_id = %s AND run_id = %s ORDER BY kind",
        account,
        run_id,
    )
    assert [k for k, *_ in stored] == ["evidence", "jobs", "people", "summary", "tech"]
    assert all(src == "workspace_import" for *_, src in stored)
    assert {fn: sha for _, fn, sha, _ in stored} == {
        fn: hashlib.sha256(body).hexdigest() for fn, body in files.items()
    }
    async with tenant_txn(tenant_a, user="user-1") as s:
        listed = await W.deliverables(s, account)
        assert sorted(d["filename"] for d in listed) == sorted(files)
        delivered = await SH.delivered_run(s, account)
        assert delivered == UUID(str(run_id)) and await SH.imported_run(s, delivered)
        assert await W.stale_kinds(s, account) == []
        found = await FT.delivered_files(s, account, ["people", "jobs", "technology"])
        assert set(found) == {"people", "jobs", "technology"}
    book = await FT.book_of(svc.payloads, tenant_a, found["people"], FT.main_sheet_of("people"))
    assert book is not None
    main = FT.main_table(book)
    assert main is not None and main.table is not None
    sheet = next(g for g in EC.sheet_grids("p", files[OWNER.PEOPLE_FILE]) if g.name == "People")
    assert [h if isinstance(h, str) else h.text for h in main.table.header] == sheet.header
    assert len(main.table.rows) == len(sheet.rows)
    # the screen's count and its table come from the file itself, never the live sheet
    assert found["people"].imported and found["people"].rows is None
    assert await FT.main_rows(svc.payloads, tenant_a, found["people"], "people") == len(sheet.rows)
    assert admin("SELECT count(*) FROM view_materializations WHERE account_id = %s", account) == [
        (0,)
    ]

    # 3. the native rows: one per sheet row, nothing folded, the linkless archive record too
    people = admin(
        "SELECT name, merged_count FROM people WHERE run_id = %s"
        " AND source_record_id NOT LIKE 'leadership:%%'",
        run_id,
    )
    assert len(people) == len(sheet.rows) and all(m == 0 for _, m in people)
    jobs_grid = next(g for g in EC.sheet_grids("j", files[OWNER.JOBS_FILE]) if g.name == "Jobs")
    hidden = next(
        g
        for g in EC.sheet_grids("j", files[OWNER.JOBS_FILE])
        if g.name == "Hidden title roles read"
    )
    postings = admin("SELECT title, url, merged_count FROM job_postings WHERE run_id = %s", run_id)
    assert len(postings) == len(jobs_grid.rows) + len(hidden.rows)
    assert all(m == 0 for *_, m in postings)
    title_col = jobs_grid.header.index("Title")
    hidden_col = hidden.header.index("Title")
    assert sorted(t for t, *_ in postings) == sorted(
        [str(cells[title_col]) for _, cells in jobs_grid.rows]
        + [str(cells[hidden_col]) for _, cells in hidden.rows]
    )
    assert any(url is None for _, url, _ in postings), "the archive record without a link"

    # 4. Download everything: the files, their own tables, an import report, no repair
    writer = _Writer()
    src = D.Sources(tenant_txn, svc.payloads, svc.blobs)
    from account_intelligence.db.session import acting_as

    with acting_as("user-1"):
        filled = await D._fill(
            tenant_a, account, "user-1", OWNER.ACCOUNT_NAME, writer, tmp_path, src
        )
    members = writer.members
    for fn, body in files.items():
        assert members[f"Deliverables/{fn}"] == body
    stem = OWNER.PEOPLE_FILE.rsplit(".", 1)[0]
    assert members[f"Tables/{OWNER.PEOPLE_FILE}"] == files[OWNER.PEOPLE_FILE]
    rows = list(csv.reader(io.StringIO(members[f"Tables/{stem} - People.csv"].decode("utf-8-sig"))))
    assert rows[0] == sheet.header
    assert len(rows) == 1 + len(sheet.rows)
    assert rows[1][: len(sheet.rows[0][1])] == [
        "" if c is None else str(c) for c in sheet.rows[0][1]
    ]
    summary_stem = OWNER.SUMMARY_FILE.rsplit(".", 1)[0]
    assert f"Tables/{summary_stem} tables.xlsx" in members
    assert any(n.startswith(f"Tables/{summary_stem} - ") and n.endswith(".csv") for n in members)
    assert "Logs/import_report.md" in members
    assert not any(n.endswith("repair_report.md") or n.endswith("exclusions.csv") for n in members)
    assert "No repair stage ran" in members["Logs/import_report.md"].decode("utf-8")
    assert filled.tables_text == D.IMPORTED_TABLES_TEXT
    assert any("no repair stage ran" in n for n in filled.notes)
    assert filled.profile.run_date.isoformat() == OWNER.RESEARCH_DATE
