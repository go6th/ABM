"""An uploaded deliverable workbook becomes the account's job postings, people and technologies
(``documents.deliverables``, 2026-10-16): recognised by its header row (by name, or from the
first header row of a sheet that opens with a title block), recorded as private Internal facts
citing the file, counted and listed by the workspace and its sheets, idempotent, and never
storing a work email. SYNTHETIC files only."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import acting_as, tenant_txn
from tests.conftest import _make_account
from tests.documents.world import admin, doc_row, service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
USER = "test-owner"  # the account's owner (tests.conftest._make_account)


def _book(sheets: dict[str, list[list[Any]]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def jobs_book() -> bytes:
    """The Job Postings workbook as the skill writes it: a Summary title sheet and Jobs."""
    return _book(
        {
            "Summary": [["Northwind: job postings that name lane platforms, version 2"]],
            "Jobs": [
                ["Requisition", "Title", "Platforms named", "Lane families", "Status",
                 "First seen", "Last seen", "Location", "Seniority"],
                ["JR-1", "Senior Salesforce Developer", "Salesforce", "Salesforce", "Open",
                 "2026-09-22", "2026-10-01", "Dublin, Ireland", "mid_senior"],
                ["JR-2", "Data Platform Engineer", "Snowflake, AWS", "Data and AI", "Closed",
                 "2026-08-02", "2026-09-30", "Pune, India", "senior"],
                ["JR-3", "", "Salesforce", "Salesforce", "Open", "2026-09-01", "", "Remote", ""],
            ],
        }
    )  # fmt: skip


def people_book() -> bytes:
    """The People List workbook: Summary, then People with a work email column."""
    return _book(
        {
            "Summary": [["Northwind people list for account research, version 2"]],
            "People": [
                ["No.", "Name", "Title as reported", "Level", "Lane", "Priority",
                 "LinkedIn link", "Work email"],
                [1, "Akash Kapoor", "Sr. Director, AI Strategy", "Senior Director", "AI", "A",
                 "https://www.linkedin.com/in/akash-k", "akash@northwind.example"],
                [2, "Andrea Simon", "Director, Support Applications", "Director",
                 "Salesforce and GTM systems", "B", "https://www.linkedin.com/in/andrea-s",
                 "andrea@northwind.example"],
            ],
        }
    )  # fmt: skip


def tech_book() -> bytes:
    """The Tech Stack and Teams workbook: every sheet opens with a title block before its header."""
    return _book(
        {
            "Platform Ranking": [
                ["Northwind: platforms seen in job postings, ranked A, B, C"],
                ["Postings that name at least one platform", 120],
                ["How the rank is set. A: named in 15% or more of the base."],
                ["Rank", "Platform", "Category", "Lane", "Postings naming it", "Open now",
                 "Last seen"],
                ["A", "Salesforce", "CRM", "Salesforce", 41, 7, "2026-10-12"],
                ["B", "Snowflake", "Data", "Data and AI", 12, 2, "2026-10-10"],
            ],
            "Confirmed Stack": [
                ["Northwind: technologies confirmed in use, by business function"],
                ["Source: confirmed used stack from postings"],
                ["Business function", "Category", "Technology", "Postings confirming use"],
                ["Engineering and R&D", "Languages", "Python", 88],
            ],
            "Platform Teams": [
                ["Northwind: teams whose postings name the lane platforms"],
                ["Team", "Team path", "Postings since 11 Oct 2025", "Lane platforms named",
                 "Most named technologies", "Top locations"],
                ["Sales Development team", "Sales Development team", 7, "Salesforce (9)",
                 "Salesforce (9), ZoomInfo (4)", "United States"],
            ],
        }
    )  # fmt: skip


async def _counts(tenant: UUID, account: UUID) -> dict[str, Any]:
    from account_intelligence.replica.workspace import workspace

    with acting_as(USER):
        async with tenant_txn(tenant) as s:
            return (await workspace(s, account, USER, datetime.now(UTC)))["counts"]


async def _sheet(tenant: UUID, account: UUID, key: str) -> list[dict[str, Any]]:
    """The sheet's rows as its SQL returns them (the page reader formats them for display)."""
    from sqlalchemy import text

    from account_intelligence.replica import sheets

    with acting_as(USER):
        async with tenant_txn(tenant) as s:
            rows = await s.execute(
                text(f"SELECT * FROM ({sheets.SHEETS[key].base}) s"),  # noqa: S608 allow-listed
                {"a": account, "u": USER, "now": datetime.now(UTC)},
            )
            return [dict(r) for r in rows.mappings().all()]


async def _upload_and_index(svc: Any, tenant: UUID, account: UUID, name: str, data: bytes) -> UUID:
    with acting_as(USER):
        done = await upload(svc, tenant, name, data, account)
        doc = UUID(done["document_id"])
        assert await svc.process(tenant, doc) == "indexed"
    return doc


async def test_uploaded_deliverable_workbooks_show_as_job_postings_people_and_tech(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    before = await _counts(tenant_a, account)
    assert (before["jobs"], before["people"], before["sources"]) == (0, 0, 0)
    jobs = await _upload_and_index(
        svc, tenant_a, account, "Northwind_Job_Postings.xlsx", jobs_book()
    )
    await _upload_and_index(svc, tenant_a, account, "Northwind_People_List.xlsx", people_book())
    after = await _counts(tenant_a, account)
    # two postings (the row without a title is skipped), two people, two files as sources
    assert (after["jobs"], after["people"], after["sources"]) == (2, 2, 2)
    rows = await _sheet(tenant_a, account, "jobs")
    by_title = {r["title"]: r for r in rows}
    sf = by_title["Senior Salesforce Developer"]
    assert (sf["lanes"], sf["location"], sf["status"]) == ("Salesforce", "Dublin, Ireland", "Open")
    assert str(sf["posted"]).startswith("2026-09-22")
    assert by_title["Data Platform Engineer"]["status"] == "Closed"
    people = {r["name"]: r for r in await _sheet(tenant_a, account, "people")}
    assert people["Akash Kapoor"]["title"] == "Sr. Director, AI Strategy"
    assert people["Akash Kapoor"]["priority"] == "A"
    assert people["Andrea Simon"]["area"] == "Salesforce and GTM systems"
    sources = await _sheet(tenant_a, account, "sources")
    assert {r["title"] for r in sources if r["code"] == "File"} == {
        "Northwind_Job_Postings.xlsx",
        "Northwind_People_List.xlsx",
    }
    # the facts are private, Internal, keyed like the graph's, and cite the file and the row
    facts = admin(
        "SELECT attribute_key, visibility, product_grade, value_json::text FROM facts"
        " WHERE account_id = %s ORDER BY attribute_key",
        account,
    )
    keys = [k for k, _, _, _ in facts]
    assert all(v == "private" and g == "internal" for _, v, g, _ in facts)
    assert sum(k.startswith("jobs.job_postings_read_classified.") for k in keys) == 2
    assert sum(k.startswith("people.full_profiles_senior_people.") for k in keys) == 2
    # a work email is never stored, in a value, a quote or a field
    assert admin(
        "SELECT count(*) FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
        " WHERE f.account_id = %s AND (f.value_json::text ILIKE '%%northwind.example%%'"
        " OR e.supporting_sentence ILIKE '%%northwind.example%%')",
        account,
    ) == [(0,)]
    assert admin(
        "SELECT count(*) FROM fact_evidence e JOIN facts f ON f.id = e.fact_id"
        " WHERE f.account_id = %s AND e.document_id = %s AND e.document_location ? 'row'",
        account,
        jobs,
    ) == [(2,)]
    # importing the same document again writes nothing new
    from documents.deliverables import import_document

    with acting_as(USER):
        async with tenant_txn(tenant_a) as s:
            from documents import repo

            row = await repo.get(s, jobs)
        counts = await import_document(tenant_txn, tenant_a, row, now=datetime.now(UTC))
    assert counts["jobs_rows"] == 2 and counts["jobs_written"] == 0
    assert (await _counts(tenant_a, account))["jobs"] == 2
    assert doc_row(jobs)["status"] == "indexed"


async def test_a_title_block_sheet_is_read_from_its_header_row(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    await _upload_and_index(
        svc, tenant_a, account, "Northwind_Tech_Stack_and_Teams.xlsx", tech_book()
    )
    counts = await _counts(tenant_a, account)
    assert counts["tech"] == 4  # two ranked platforms, one confirmed technology, one team
    rows = {r["technology"]: r for r in await _sheet(tenant_a, account, "technology")}
    assert rows["Salesforce"]["sources"] == 41 and rows["Salesforce"]["check"] == "A"
    assert rows["Python"]["sources"] == 88
    assert "Sales Development team" in rows
    assert admin(
        "SELECT count(*) FROM facts WHERE account_id = %s AND attribute_key LIKE 'tech.%%'", account
    ) == [(4,)]
    # title rows and the header rows themselves are not facts
    assert admin(
        "SELECT count(*) FROM facts WHERE account_id = %s AND value_text IN ('Rank', 'Platform',"
        " 'Technology', 'Team')",
        account,
    ) == [(0,)]


async def test_header_matching_is_exact_before_prefix() -> None:
    from documents.deliverables import KINDS, match, norm

    assert norm(" Lane  Platforms named (postings) ") == "lane platforms named postings"
    jobs = match(["Requisition", "Title", "Status", "Location"])
    assert jobs is not None and jobs.kind == "jobs"
    people = match(["No.", "Name", "Title as reported", "Priority"])
    assert people is not None and people.kind == "people"
    teams = match(["Team", "Team path", "Postings since 11 Oct 2025"])
    assert teams is not None and teams.note == "team"
    assert match(["Account", "Contact Name", "Stage", "Next Step"]) is None  # a CRM export
    assert match(["What this is", "one deduplicated list"]) is None
    assert len({k.kind for k in KINDS}) == 3
    from documents.deliverables import segments, title_prefix

    title = "Northwind: platforms seen in job postings, ranked A, B, C"
    rows = [
        f"{title}: Postings that name a platform; column 2: 120",
        f"{title}: Rank; column 2: Platform",
    ]
    prefix = title_prefix([r.split("; ")[0] for r in rows])
    assert prefix == f"{title}: "
    assert segments(rows[1], prefix) == [(title, "Rank"), ("column 2", "Platform")]
    assert segments("Source: confirmed; column 2: x", None) == [
        ("Source", "confirmed"),
        ("column 2", "x"),
    ]


async def test_documents_indexed_before_the_import_are_backfilled_by_the_sweep(
    tenant_a: UUID, tmp_path: Path
) -> None:
    from documents.deliverables import backfill_imports
    from tests.documents import fixtures

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    jobs = await _upload_and_index(
        svc, tenant_a, account, "Northwind_Job_Postings.xlsx", jobs_book()
    )
    pdf = await _upload_and_index(svc, tenant_a, account, "call.pdf", fixtures.meeting_pdf())
    # both were read at indexing time: marked, and only the workbook made an import run
    assert admin(
        "SELECT count(*) FROM documents WHERE id IN (%s, %s) AND imported_at IS NOT NULL", jobs, pdf
    ) == [(2,)]
    assert admin(
        "SELECT count(*) FROM account_runs WHERE account_id = %s"
        " AND finished_reason = 'file_import'",
        account,
    ) == [(1,)]
    # a document from before the import: indexed, bound, unmarked, with none of its rows recorded
    admin(
        "DELETE FROM grade_events WHERE fact_id IN (SELECT id FROM facts WHERE account_id = %s)",
        account,
    )
    admin(
        "DELETE FROM fact_evidence WHERE fact_id IN (SELECT id FROM facts WHERE account_id = %s)",
        account,
    )
    admin("DELETE FROM facts WHERE account_id = %s", account)
    admin("UPDATE documents SET imported_at = NULL WHERE id IN (%s, %s)", jobs, pdf)
    assert (await _counts(tenant_a, account))["jobs"] == 0
    done = await backfill_imports(datetime.now(UTC), limit=10)
    assert done["documents"] >= 2 and done["facts"] >= 2
    assert (await _counts(tenant_a, account))["jobs"] == 2
    assert admin(
        "SELECT count(*) FROM documents WHERE id IN (%s, %s) AND imported_at IS NULL", jobs, pdf
    ) == [(0,)]
    # the next pass has nothing left for this client's two documents
    again = await backfill_imports(datetime.now(UTC), limit=10)
    assert admin(
        "SELECT count(*) FROM facts WHERE account_id = %s AND attribute_key LIKE 'jobs.%%'", account
    ) == [(2,)]
    assert again["facts"] == 0 or (await _counts(tenant_a, account))["jobs"] == 2


async def test_two_sweeps_never_read_one_document_twice_and_a_failed_import_is_given_back(
    tenant_a: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from documents import deliverables, repo

    account = _make_account(tenant_a, f"northwind-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    jobs = await _upload_and_index(
        svc, tenant_a, account, "Northwind_Job_Postings.xlsx", jobs_book()
    )
    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", jobs)
    now = datetime.now(UTC)
    with acting_as(USER):
        async with tenant_txn(tenant_a) as s:
            first = await repo.claim_imports(s, now, 10)
        async with tenant_txn(tenant_a) as s:
            second = await repo.claim_imports(s, now, 10)
    assert [str(r.id) for r in first] == [str(jobs)] and second == []  # claimed once
    # a failing import gives the document back for the next pass, with the cause logged
    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", jobs)

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("storage away")

    monkeypatch.setattr(deliverables, "import_document", boom)
    done = await deliverables.backfill_imports(now, limit=10)
    assert done["documents"] == 0
    assert admin("SELECT imported_at FROM documents WHERE id = %s", jobs) == [(None,)]
    monkeypatch.undo()
    done = await deliverables.backfill_imports(datetime.now(UTC), limit=10)
    assert done["documents"] >= 1
    assert admin("SELECT count(*) FROM documents WHERE id = %s AND imported_at IS NULL", jobs) == [
        (0,)
    ]
    assert deliverables.error_code(RuntimeError("x")) == "RuntimeError"
