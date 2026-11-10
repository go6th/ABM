"""A workspace zip through the production path with the members a hand-built package carries
besides its deliverables (``documents.workspace``, 2026-11-03): a ``build/`` intermediate and a
``data/`` raw pull whose column names look like the Jobs and People sheets, a build script and a
Word file named ``_Owner_Only`` (read like any file since the owner-only role was removed,
2026-11-08). Each deliverable row imports once (the twins never do), the raw pull is still a table
the tools can query, and the build members are listed as skipped with their reasons, never stored
as passages. SYNTHETIC files only."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.db.session import tenant_txn
from documents import tables as T
from documents.coverage import coverage
from tests.conftest import _make_account
from tests.documents import workspace_zip
from tests.documents.test_deliverables_import import _counts, _sheet
from tests.documents.world import admin, doc_row, service, upload
from tests.research_files import fixtures as rf_fixtures

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

F = workspace_zip.FOLDER
BUILD_TWIN = f"{F}/build/jobs_twin.json"
DATA_TWIN = f"{F}/data/people_twin.json"
SCRIPT = f"{F}/build/third_party_scan.py"
OWNER_ONLY = f"{F}/Holdings_Owner_Only.docx"


def _zip_with_twins() -> bytes:
    """The synthetic workspace zip plus the members under test."""
    base = zipfile.ZipFile(io.BytesIO(workspace_zip.workspace_zip_bytes()))
    jobs_twin = [
        {"Requisition": f"T-{i}", "Title": f"Twin posting {i}", "Platforms named": "Salesforce",
         "Status": "Open", "First seen": "2026-09-01", "Location": "Remote"}
        for i in range(1, 4)
    ]  # fmt: skip
    people_twin = [
        {"Name": f"Twin Person {i}", "Title": "Director, Twin Systems", "Level": "Director",
         "Priority": "A"}
        for i in range(1, 3)
    ]  # fmt: skip
    _, docx = rf_fixtures.valid()["docx"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in base.infolist():
            data = base.read(info)
            if info.filename.endswith(workspace_zip.JOBS_FILE):
                data = _with_posting_links(data)
            if info.filename.endswith(workspace_zip.PEOPLE_FILE):
                data = _with_profile_link(data)
            z.writestr(info, data)
        z.writestr(BUILD_TWIN, json.dumps(jobs_twin, indent=1))
        z.writestr(DATA_TWIN, json.dumps(people_twin, indent=1))
        z.writestr(OWNER_ONLY, docx)
        z.writestr(SUMMARY, _summary_docx())
        for name, data in _raw_members().items():
            z.writestr(name, data)
    return out.getvalue()


SUMMARY = f"{F}/deliverables/Holdings_Account_Summary.docx"


def _summary_docx() -> bytes:
    """A synthetic Account Summary with the tables and sections the Word import reads."""
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()

    def table(rows: list[list[str]]) -> None:
        t = doc.add_table(rows=len(rows), cols=len(rows[0]))
        for r, cells in enumerate(rows):
            for c, value in enumerate(cells):
                t.cell(r, c).text = value

    def link_cell(cell: Any, label: str, url: str) -> None:
        para = cell.paragraphs[0]
        r_id = para.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = label
        run.append(t)
        link.append(run)
        para._p.append(link)

    doc.add_heading("1. The account in plain words", level=1)
    doc.add_heading("The account picture", level=2)
    doc.add_paragraph("Synthetic Holdings runs Salesforce as its CRM and SAP for finance (S1, S2).")
    doc.add_heading("What stands out", level=2)
    doc.add_paragraph("A new CIO started in September (S2).")
    doc.add_heading("2. Recent news (newest first)", level=1)
    table(
        [
            ["Date", "What happened", "Read depth", "Source", "Why it matters"],
            ["21 Sep 2026", "New CIO announced", "Read", "S2", "Tier 1: new owner of IT"],
            ["1 Jun 2026", "Factory opened", "Headline only", "S1", "Capacity"],
        ]
    )
    doc.add_heading("4. Signals", level=1)
    doc.add_heading("Tier 1, budget proximate", level=2)
    table(
        [
            ["Finding", "Grade", "Lanes", "Date", "Sources"],
            ["New CIO with an AI brief", "Confirmed", "All lanes", "30 Sep 2026", "S2"],
            ["CRM harmonisation hiring", "Probable", "Salesforce", "Mar 2026", "S1, S2"],
            # a Hypothesis with no question in the file gets a plain one (UCB 2026-11-04)
            ["Possible CRM platform move", "Hypothesis", "Salesforce", "Oct 2026", "S2"],
        ]
    )
    doc.add_heading("6. People", level=1)
    doc.add_heading("Key people", level=2)
    table(
        [
            ["Name", "Title as reported", "Lane", "Priority", "Link", "Note (from the profile)"],
            [
                "Avery Example",
                "VP, Commercial Systems",
                "Salesforce",
                "A",
                "Profile",
                "Owns the Salesforce org.",
            ],
        ]
    )
    doc.add_heading("7. Hypotheses to test", level=1)
    doc.add_paragraph("The CRM harmonisation is funded and in design (S2).")
    doc.add_paragraph("Confirming question: Is the harmonisation a funded program with a partner?")
    doc.add_heading("Cautions for sensitive findings", level=2)
    doc.add_paragraph("Never open with the recall.")
    doc.add_heading("8. Questions for a first conversation", level=1)
    doc.add_paragraph("Which Salesforce clouds are licensed today?")
    doc.add_heading("9. Research coverage tracker", level=1)
    doc.add_heading("Pass 1, summary homework", level=2)
    table(
        [
            ["Source type", "Status", "Priority", "Needs", "What was done (proof)", "What remains"],
            [
                "Basic fit check",
                "Done",
                "High",
                "Nothing",
                "Four segments, six lanes present",
                "None",
            ],
            [
                "Platform vendor account team",
                "Blocked",
                "High",
                "Account owner",
                "Not researched by design",
                "Name the account teams",
            ],
        ]
    )
    doc.add_heading("10. Source links", level=1)
    t = doc.add_table(rows=3, cols=5)
    for c, value in enumerate(["ID", "Source", "Domain", "How retrieved", "What it supports"]):
        t.cell(0, c).text = value
    t.cell(1, 0).text = "S1"
    link_cell(t.cell(1, 1), "Factory release", "https://news.holdings.example/factory")
    t.cell(1, 2).text = "news.holdings.example"
    t.cell(1, 3).text = "Read in full"
    t.cell(1, 4).text = "The plant opening"
    t.cell(2, 0).text = "S2"
    link_cell(t.cell(2, 1), "CIO appointment", "https://news.holdings.example/cio")
    t.cell(2, 2).text = "news.holdings.example"
    t.cell(2, 3).text = "Read in full"
    t.cell(2, 4).text = "The CIO appointment"
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


PAGE = f"{F}/data/releases/2026-09-21_cio-appointment.md"
LIVE_PAGE = f"{F}/data/releases/2026-10-19_live_99001.md"
DETECTIONS = f"{F}/data/techstack_detections.json"
NEWS = f"{F}/data/news_events.json"
CONNECTIONS = f"{F}/data/connections.json"
LEADERSHIP = f"{F}/data/leadership.json"
PROFILES = f"{F}/data/profiles_full.json"
PULL_LOG = f"{F}/notes/pull_log_phase1.md"
SENSE = f"{F}/notes/sense_check.md"


def _raw_members() -> dict[str, bytes]:
    """Captured pages, raw pulls and notes the way a research workspace carries them."""
    return {
        PAGE: (
            b"title: Holdings names a Chief Information Officer\n"
            b"url: https://news.holdings.example/cio\n"
            b"date: 2026-09-21\n"
            b"source: 6th agents\n\n"
            b"# Holdings names a Chief Information Officer\n\n"
            b"Synthetic Holdings today named a new CIO, effective 30 September 2026.\n"
        ),
        LIVE_PAGE: (
            b"title: Salesforce Administrator at Synthetic Holdings\n"
            b"url: https://jobs.holdings.example/job/99001\n"
            b"date: 2026-10-19\n"
            b"source: 6th agents\n\n"
            b"Salesforce Administrator\n\nRuns the org.\n"
        ),
        DETECTIONS: json.dumps(
            {
                "source": "6th agents",
                "detections": [
                    {
                        "technology": "Salesforce",
                        "first_seen_at": "2025-11-01T00:00:00Z",
                        "last_seen_at": "2026-10-19T00:00:00Z",
                        "score": 0.97,
                        "source_count": 40,
                        "behind_firewall": False,
                        "locations": [{"city": "Dublin", "country": "Ireland"}],
                        "seen_on_job_openings": 38,
                        "seen_on_subpages": 2,
                    },
                    {
                        "technology": "Kinaxis",
                        "first_seen_at": "2026-01-01T00:00:00Z",
                        "last_seen_at": "2026-10-01T00:00:00Z",
                        "score": 0.4,
                        "source_count": 2,
                        "behind_firewall": True,
                        "locations": [],
                        "seen_on_job_openings": 2,
                        "seen_on_subpages": 0,
                    },
                ],
            }
        ).encode(),  # fmt: skip
        NEWS: json.dumps(
            {
                "events": [
                    {
                        "id": "ev-1",
                        "category": "hires",
                        "headline": "Holdings names a CIO",
                        "summary": "A new CIO starts on 30 September.",
                        "found_at": "2026-09-21T10:00:00Z",
                        "effective_date": "2026-09-30",
                        "confidence": 0.9,
                        "location": "Dublin",
                    },
                    {
                        "id": "ev-2",
                        "category": "expands_facilities",
                        "headline": "Holdings opens a plant",
                        "summary": "A plant opened in June.",
                        "found_at": "2026-06-02T10:00:00Z",
                        "effective_date": "2026-06-01",
                        "confidence": 0.8,
                    },
                ]
            }
        ).encode(),  # fmt: skip
        CONNECTIONS: json.dumps(
            {
                "connections": [
                    {
                        "category": "vendor",
                        "source_category": "partner_page",
                        "first_seen_at": "2026-03-01T00:00:00Z",
                        "last_seen_at": "2026-10-19T00:00:00Z",
                        "source_url": "https://partners.example/holdings",
                        "company1": "partners.example",
                        "company1_name": "Partners Example",
                        "company2": "holdings.example",
                        "company2_name": "Synthetic Holdings",
                    }
                ]
            }
        ).encode(),  # fmt: skip
        LEADERSHIP: json.dumps(
            {
                "executives": [
                    {
                        "name": "Avery Example",
                        "title": "VP, Commercial Systems",
                        "page_url": "https://www.holdings.example/leadership",
                    },
                    {
                        "name": "Harper Newcomer",
                        "title": "Chief Financial Officer",
                        "page_url": "https://www.holdings.example/leadership",
                    },
                ]
            }
        ).encode(),  # fmt: skip
        # a profile without certifications (most real ones): stored with NULL certifications,
        # never an empty list (ck_person_profiles_certs rolled back the whole raw part, UCB)
        PROFILES: json.dumps(
            {
                "profiles": [
                    {
                        "linkedin_url": AVERY_PROFILE,
                        "profile": {
                            "fullName": "Avery Example",
                            "currentTitle": "VP, Commercial Systems",
                            "currentEmployer": "Synthetic Holdings",
                            "experiences": [
                                {
                                    "company": "Synthetic Holdings",
                                    "title": "VP",
                                    "startDate": "2021-03",
                                },
                            ],
                        },
                    }
                ]
            }
        ).encode(),  # fmt: skip
        PULL_LOG: (
            b"# Holdings phase 1 pull log\n\n"
            b"Run date: 2026-10-19.\n\n"
            b"## Calls\n\n"
            b"| Step | Tool | Parameters as sent | Result count | Output file |\n"
            b"|---|---|---|---|---|\n"
            b"| 2.1 | open web search | queries: Holdings overview | 10 |"
            b" data/web_searches.json |\n"
            b"| 3.3 | job and news feed (technology detections) | domain=holdings.example | 2 |"
            b" data/techstack_detections.json |\n"
        ),
        SENSE: b"# Sense check\n\nThe entity is right.\n",
    }


def _with_posting_links(xlsx: bytes) -> bytes:
    """The synthetic Jobs sheet with a web address behind every "Open posting" cell (the
    reference workbook links each row; a posting row without an address is not stored)."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Jobs"]
    header = [c.value for c in ws[1]]
    link_col = header.index("Link") + 1
    req_col = header.index("Requisition") + 1
    for row in range(2, ws.max_row + 1):
        req = ws.cell(row=row, column=req_col).value
        cell = ws.cell(row=row, column=link_col)
        cell.value = "Open posting"
        cell.hyperlink = f"https://jobs.holdings.example/job/{req or f'row{row}'}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


AVERY_PROFILE = "https://www.linkedin.com/in/avery-example"


def _with_profile_link(xlsx: bytes) -> bytes:
    """The synthetic People sheet with a LinkedIn link on Avery's row, so the raw profile pull
    has a person to attach to."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["People"]
    col = ws.max_column + 1
    ws.cell(row=1, column=col).value = "LinkedIn link"
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=1).value == "Avery Example":
            ws.cell(row=row, column=col).value = AVERY_PROFILE
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _facts_by_member(account: UUID) -> dict[str, int]:
    rows = admin(
        "SELECT e.document_location->>'member', count(*) FROM facts f"
        " JOIN fact_evidence e ON e.fact_id = f.id"
        " WHERE f.account_id = %s AND f.flags ? 'workspace_import'"
        " GROUP BY 1",
        account,
    )
    return {str(member): int(n) for member, n in rows}


async def test_a_workspace_zip_imports_each_deliverable_row_once_and_skips_build_files(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """See the module docstring."""
    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    uploaded = _zip_with_twins()
    done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, uploaded, account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    assert doc_row(doc)["status"] == "indexed"

    # a workspace package goes to the native tables, once, never to private facts: the Jobs
    # sheet's postings are job_postings of the file-import run (the jobs-shaped build twin
    # and the people-shaped data twin import nothing although their headers match)
    jobs_member = f"{F}/deliverables/{workspace_zip.JOBS_FILE}"
    people_member = f"{F}/deliverables/{workspace_zip.PEOPLE_FILE}"
    assert set(_facts_by_member(account)) <= {SUMMARY}  # only the Summary's findings are facts
    (run_id,) = admin(
        "SELECT id FROM account_runs WHERE account_id = %s AND finished_reason = 'file_import'",
        account,
    )[0]
    postings = admin(
        "SELECT posting_key, title, status, lanes, platforms, posting_class, entity_tag, locations,"
        " merged_count, full_text_read, text_ref FROM job_postings WHERE run_id = %s",
        run_id,
    )
    # 644 sheet rows, 644 posting rows: nothing is folded (exact copy, owner 2026-11-05); a
    # requisition the sheet lists twice keeps both rows, the second keyed by its sheet row
    assert len(postings) == 644
    assert not any("Twin" in str(t) for _, t, *_ in postings)
    assert all(k.startswith(("id:", "url:", "weak:")) for k, *_ in postings)
    assert all(m == 0 for *_, m, _, _ in postings)
    assert any("#" in k for k, *_ in postings)  # at least one requisition was on two rows
    assert (
        admin("SELECT count(*) FROM job_posting_platform_sentences WHERE run_id = %s", run_id)[0][0]
        > 0
    )
    assert (
        admin("SELECT count(*) FROM job_posting_classifications WHERE run_id = %s", run_id)[0][0]
        > 0
    )
    people_rows = admin(
        "SELECT name, job_level, scope_status, priority FROM people WHERE run_id = %s"
        " ORDER BY name",
        run_id,
    )
    assert [n for n, *_ in people_rows] == [
        "Avery Example", "Blake Sample", "Casey Placeholder", "Devon Specimen", "Emery Fictional",
        "Harper Newcomer",  # the leadership page's newcomer (phase 4); Avery is not added twice
    ]  # fmt: skip
    assert {lvl for _, lvl, *_ in people_rows} <= {
        "vp", "director", "head", "senior_director", "other"  # "other": the leadership newcomer
    }  # fmt: skip
    assert admin(
        "SELECT name, certifications, certifications_provided FROM person_profiles"
        " WHERE run_id = %s",
        run_id,
    ) == [("Avery Example", None, False)]
    # the deep-dive counts and sheets read them like a research's rows
    counts = await _counts(tenant_a, account)
    assert counts["people"] == 6 and counts["jobs"] > 0
    jobs_sheet = await _sheet(tenant_a, account, "jobs")
    assert len(jobs_sheet) == len(postings) and all(r["title"] for r in jobs_sheet)
    # Ask gets the imported rows as research tables (the UCB thread of 2026-11-04 had none):
    # the people and postings the import wrote, queryable whole
    from account_intelligence.replica import research_tables as RT

    async with tenant_txn(tenant_a, user="user-1") as s:
        offered = {t.name: t for t in await RT.research_tables(s, [account])}
        assert offered["Research: People"].row_count == len(people_rows)
        assert offered["Research: Job postings"].row_count == len(postings)
        open_rows = await T.run(
            s,
            T.parse(
                offered["Research: Job postings"],
                {"where": [{"column": "status", "op": "eq", "value": "Open"}]},
            ),
        )
    assert open_rows["row_count"] == sum(1 for _, _, st, *_ in postings if st == "open")
    assert admin(
        "SELECT count(*) FROM facts WHERE account_id = %s AND value_text LIKE 'Twin %%'", account
    ) == [(0,)]

    # the raw pull is still a table the tools can query; the build members are not stored at all,
    # the _Owner_Only file is read like any other
    async with tenant_txn(tenant_a, user="user-1") as s:
        found = await T.list_tables(s, [account])
        cover = await coverage(s, [account])
    names = {t.table for t in found}
    assert any(n.endswith(f"{DATA_TWIN}:$") or DATA_TWIN in n for n in names), names
    assert not any(BUILD_TWIN in n for n in names)
    members = {
        str(m)
        for (m,) in admin(
            "SELECT DISTINCT coalesce(location->>'member', location->'from'->>'member')"
            " FROM document_passages WHERE document_id = %s",
            doc,
        )
        if m
    }
    assert DATA_TWIN in members and jobs_member in members and OWNER_ONLY in members
    assert not members & {BUILD_TWIN, SCRIPT}

    # the coverage report names what was skipped and why, once per member
    entry = next(e for e in cover if e.get("document") == workspace_zip.ZIP_NAME)
    skipped = {m["member"]: m["reason"] for m in entry["members_skipped"]}
    assert skipped == {
        BUILD_TWIN: "a build script or intermediate, not research data",
        SCRIPT: "a build script or intermediate, not research data",
    }
    assert entry["members_skipped_total"] == 2
    assert entry["members_read"] == len(members)

    # the Account Summary feeds the register, the findings, the sections, the tracker and the
    # people's notes (phase 3): grades as written, private (the zip is not the product's own)
    sources_rows = admin(
        "SELECT source_code, url, aliases, publisher FROM sources WHERE account_id = %s"
        " ORDER BY source_code",
        account,
    )
    by_alias = {a: code for code, _, aliases, _ in sources_rows for a in aliases}
    assert set(by_alias) >= {"S1", "S2"}
    assert any(u == "https://news.holdings.example/cio" for _, u, *_ in sources_rows)
    facts_rows = admin(
        "SELECT attribute_key, product_grade, visibility, value_json->>'why_it_matters',"
        " value_json->'source_codes', flags FROM facts WHERE account_id = %s"
        " AND flags ? 'imported_grade' ORDER BY attribute_key",
        account,
    )
    keys = {k for k, *_ in facts_rows}
    assert any(k.startswith("signals.tier_1.") for k in keys)
    assert any(k.startswith("news.company_news.") for k in keys)
    assert all(v == "private" for _, _, v, *_ in facts_rows)
    grades = {k: g for k, g, *_ in facts_rows}
    assert grades[next(k for k in keys if k.startswith("signals.tier_1.new_cio"))] == "confirmed"
    assert grades[next(k for k in keys if k.startswith("signals.tier_1.crm"))] == "probable"
    assert admin(
        "SELECT confirming_question, flags ? 'question_generated' FROM facts"
        " WHERE account_id = %s AND product_grade = 'hypothesis'",
        account,
    ) == [("Is this right: Possible CRM platform move?", True)]
    news = next(r for r in facts_rows if r[0].startswith("news.company_news.new_cio"))
    assert news[3] == "Tier 1: new owner of IT" and news[4] == [by_alias["S2"]]
    sections = admin(
        "SELECT kind, text, confirming_question, cited_source_codes FROM report_sections"
        " WHERE run_id = %s ORDER BY kind, position",
        run_id,
    )
    kinds = {k for k, *_ in sections}
    assert {"narrative", "stands_out", "hypothesis", "caution", "first_call_question"} <= kinds
    hyp = next(r for r in sections if r[0] == "hypothesis")
    assert hyp[2] == "Is the harmonisation a funded program with a partner?"
    assert hyp[3] == [by_alias["S2"]]
    tracker = admin(
        "SELECT row_key, status, reason_code, needs, what_remains FROM coverage_rows"
        " WHERE run_id = %s AND status IS NOT NULL ORDER BY row_key",
        run_id,
    )
    assert ("basic_fit_check", "done", None, "Nothing", "None") in tracker
    assert ("platform_vendor_account_team", "blocked", "not_available_yet", "Account owner",
            "Name the account teams") in tracker  # fmt: skip
    assert admin(
        "SELECT note FROM people WHERE run_id = %s AND name = 'Avery Example'", run_id
    ) == [("Owns the Salesforce org.",)]

    # the captured pages, raw pulls and notes (phase 4): archive copies, detections with the
    # feed's fields, news events, connections, leadership people, notes and the pull log
    archived = admin(
        "SELECT url, kind, title FROM link_archive WHERE account_id = %s ORDER BY url", account
    )
    assert ("https://jobs.holdings.example/job/99001", "posting",
            "Salesforce Administrator at Synthetic Holdings") in archived  # fmt: skip
    assert ("https://news.holdings.example/cio", "page",
            "Holdings names a Chief Information Officer") in archived  # fmt: skip
    detections = {
        t: (sc, s_on, fw, loc)
        for t, sc, s_on, fw, loc in admin(
            "SELECT technology, score, seen_on_postings, behind_firewall, locations"
            " FROM tech_detections WHERE run_id = %s",
            run_id,
        )
    }
    assert float(detections["Salesforce"][0]) == 0.97 and detections["Salesforce"][1] == 38
    assert detections["Salesforce"][3] == ["Dublin, Ireland"]
    assert detections["Kinaxis"][2] is True
    news_rows = admin(
        "SELECT event_id, category, headline, effective_date::text FROM news_events"
        " WHERE run_id = %s ORDER BY event_id",
        run_id,
    )
    assert news_rows == [
        ("ev-1", "hires", "Holdings names a CIO", "2026-09-30"),
        ("ev-2", "expands_facilities", "Holdings opens a plant", "2026-06-01"),
    ]
    assert admin(
        "SELECT company_domain, company_name, category FROM org_connections WHERE run_id = %s",
        run_id,
    ) == [("partners.example", "Partners Example", "vendor")]
    assert admin(
        "SELECT origin, scope_status, note FROM people WHERE run_id = %s"
        " AND name = 'Harper Newcomer'",
        run_id,
    ) == [("mention", "review", "On the company's leadership page.")]
    assert counts["people"] == 6  # the five of the workbook plus the leadership page's newcomer
    notes = {
        k: t for k, t in admin("SELECT kind, title FROM research_notes WHERE run_id = %s", run_id)
    }
    assert notes == {
        "pull_log": "pull_log_phase1.md",
        "sense_check": "sense_check.md",
        "note": "handoff.md",
        "restore": "RESTORE.md",
    }
    assert admin(
        "SELECT step, tool_role, result_count, output_file FROM pull_log_calls WHERE run_id = %s"
        " ORDER BY position",
        run_id,
    ) == [
        ("2.1", "open web search", "10", "data/web_searches.json"),
        ("3.3", "job and news feed (technology detections)", "2", "data/techstack_detections.json"),
    ]
    listed = await svc.members(tenant_a, doc)
    by_path = {m["member"]: m for m in listed["members"]}
    assert by_path[DETECTIONS]["rows_imported"] == 2 and by_path[NEWS]["rows_imported"] == 2
    assert by_path[PAGE]["rows_imported"] == 1 and by_path[PULL_LOG]["rows_imported"] == 3

    # importing again reads the raw pull's rows and counts them as not a deliverable
    from documents import repo
    from documents.deliverables import import_document

    async with tenant_txn(tenant_a, user="user-1") as s:
        row = await repo.get(s, doc)
    counts: dict[str, Any] = await import_document(tenant_txn, tenant_a, row, now=datetime.now(UTC))
    assert counts["rows_not_deliverable"] >= 2  # the people twin's rows at least
    assert counts["jobs_written"] == 0 and counts["people_written"] == 0  # idempotent
    assert counts["jobs_rows"] == 644 and counts["jobs_merged"] == 0  # nothing folded

    # every file inside the zip has a row of its own (document_members, 0102): its role, what
    # the read did with it and why, what it produced; the counts add up to the archive
    listed = await svc.members(tenant_a, doc)
    by_path = {m["member"]: m for m in listed["members"]}
    assert len(by_path) == len(zipfile.ZipFile(io.BytesIO(_zip_with_twins())).namelist())
    assert listed["read"] + listed["skipped"] + listed["failed"] == len(by_path)
    assert (listed["skipped"], listed["failed"]) == (2, 0)
    assert listed["read"] == entry["members_read"]
    jobs_row = by_path[jobs_member]
    assert jobs_row["role"] == "deliverable_jobs" and jobs_row["status"] == "read"
    assert jobs_row["declared_type"] == "xlsx" and jobs_row["bytes"] and jobs_row["bytes"] > 1000
    assert jobs_row["passages"] > 600 and jobs_row["tables"] >= 1 and jobs_row["reason"] is None
    # one file inside can be opened by itself, as uploaded (owner 2026-11-05: the account page
    # opens every file of the package, not only lists it); an unknown member is not found
    from documents.service import DocumentError

    original = zipfile.ZipFile(io.BytesIO(uploaded)).read(jobs_member)
    path, body = await svc.member_file(tenant_a, doc, UUID(jobs_row["id"]))
    assert path == jobs_member and body == original
    with pytest.raises(DocumentError):
        await svc.member_file(tenant_a, doc, uuid4())
    assert by_path[DATA_TWIN] == {
        **by_path[DATA_TWIN],
        "role": "raw_pull",
        "status": "read",
        "declared_type": "json",
        "tables": 1,
        "audience": "client",
    }
    assert by_path[OWNER_ONLY]["role"] == "other" and by_path[OWNER_ONLY]["status"] == "read"
    assert by_path[OWNER_ONLY]["audience"] == "client"  # its few words share a passage
    assert by_path[SCRIPT]["role"] == "build" and by_path[SCRIPT]["status"] == "skipped"
    assert by_path[BUILD_TWIN]["role"] == "build" and by_path[BUILD_TWIN]["status"] == "skipped"
    assert by_path[f"{F}/RESTORE.md"]["role"] == "restore"
    assert by_path[f"{F}/notes/handoff.md"]["role"] == "note"
    # the reconciliation on the member: file rows (every sheet of the workbook, the Summary
    # sheet's lines included) = imported + merged + not imported
    j = by_path[jobs_member]
    (member_rows,) = admin(
        "SELECT coalesce(sum(row_count), 0) FROM document_tables WHERE document_id = %s"
        " AND member = %s",
        doc,
        jobs_member,
    )[0]
    assert j["rows_imported"] == len(postings) and j["rows_merged"] == 0  # nothing folded
    assert j["rows_imported"] + j["rows_merged"] + j["rows_skipped"] == int(member_rows)
    assert by_path[people_member]["rows_imported"] == 5
    assert by_path[DATA_TWIN]["rows_imported"] == 0  # a raw pull is never imported
    assert by_path[SUMMARY]["role"] == "deliverable_summary" and by_path[SUMMARY]["tables"] >= 4
    # the file map names the members by role
    from documents.filemap import render
    from documents.sources import list_sources

    async with tenant_txn(tenant_a, user="user-1") as s:
        sources = (await list_sources(s, [account]))["sources"]
        shown = render(await T.list_tables(s, [account]), sources, cover)
    roles = {m["member"]: m["role"] for m in sources if m.get("member")}
    assert roles[jobs_member] == "deliverable_jobs" and roles[DATA_TWIN] == "raw_pull"
    assert "files:" in shown and "note" in shown
    # the status view carries the audience; a loose upload is for the account
    assert (await svc.status(tenant_a, doc))["audience"] == "client"

    # the deep dive lists the files inside the zip as a sheet of their own, with the count on
    # the account page (owner's rule: every file of the workspace is seen in the deep dive)
    from sqlalchemy import text as sql_text

    from account_intelligence.replica import sheets
    from account_intelligence.replica.workspace import workspace

    async with tenant_txn(tenant_a, user="user-1") as s:
        rows = await s.execute(
            sql_text(f"SELECT * FROM ({sheets.SHEETS['uploaded-files'].base}) s"),  # noqa: S608 allow-listed
            {"a": account, "u": "user-1", "now": datetime.now(UTC), "t": tenant_a},
        )
        shown = {r["member"]: dict(r) for r in rows.mappings().all()}
        counts = (await workspace(s, account, "user-1", datetime.now(UTC)))["counts"]
    assert set(shown) == set(by_path)
    assert counts["files"] == len(by_path)
    assert shown[jobs_member]["role"] == "job postings" and shown[jobs_member]["status"] == "read"
    assert shown[jobs_member]["reason"] == "" and shown[jobs_member]["document"].endswith(".zip")
    assert shown[OWNER_ONLY]["role"] == "other file" and shown[OWNER_ONLY]["reason"] == ""
    assert shown[SCRIPT]["reason"] == "a build script or intermediate, not research data"
    assert shown[DATA_TWIN]["role"] == "raw pull" and shown[DATA_TWIN]["tables"] == 1

    # the live sheets an imported workspace fills (phase 5): each row of the imported tables is
    # a row of its sheet, and the account page counts them
    async def sheet_rows(key: str) -> list[dict[str, Any]]:
        """The rows of a deep-dive sheet's base query, as the sheet shows them."""
        async with tenant_txn(tenant_a, user="user-1") as s:
            found = await s.execute(
                sql_text(f"SELECT * FROM ({sheets.SHEETS[key].base}) s"),  # noqa: S608 allow-listed
                {"a": account, "u": "user-1", "now": datetime.now(UTC), "t": tenant_a},
            )
            return [dict(r) for r in found.mappings().all()]

    teams = await sheet_rows("teams")
    assert counts["teams"] == len(teams) > 0
    assert all(t["team"] and t["lanes"] is not None for t in teams)
    news = await sheet_rows("news")
    assert counts["news"] == len(news) >= 2
    imported_news = {n["headline"]: n for n in news if n["imported"]}
    assert imported_news["Holdings names a CIO"]["category"] == "hires"
    assert imported_news["Holdings names a CIO"]["grade"] is None
    connections = await sheet_rows("connections")
    assert counts["connections"] == len(connections) == 1
    assert connections[0]["company"] == "Partners Example" and connections[0]["domain"] == (
        "partners.example"
    )
    found_sections = await sheet_rows("findings")
    assert counts["findings"] == len(found_sections) > 0
    assert {r["kind_key"] for r in found_sections} & {"hypothesis", "caution", "narrative"}
    calls = await sheet_rows("calls")
    assert counts["calls"] == len(calls) >= 2 and all(c["imported"] for c in calls)
    assert all(c["tool"] and c["step"] for c in calls)
    note_rows = await sheet_rows("notes")
    assert counts["notes"] == len(note_rows) == len(notes)
    assert {n["title"] for n in note_rows} == set(notes.values())
    assert {n["kind"] for n in note_rows} == {"Pull Log", "Sense Check", "Note", "Restore"}
    register = {r["link"]: r for r in await sheet_rows("sources") if r["code"].startswith("S")}
    # the Summary's signals are signal events (the Signals screen and the account page count
    # them), and the account page's key findings and one-minute version read the imported file
    async with tenant_txn(tenant_a, user="user-1") as s:
        ws = await workspace(s, account, "user-1", datetime.now(UTC))
    events = admin(
        "SELECT title, signal_type, grade, bucket FROM signal_events WHERE account_id = %s"
        " ORDER BY title",
        account,
    )
    # the Signals sheet and count also hold the research's own news findings beside the events
    # (owner decision 2026-11-06): the News sheet's rows that are not imported news events
    news_facts = [n for n in news if not n["imported"]]
    shown = len(events) + len(news_facts)
    assert events and counts["signals"] == ws["counts"]["signals"] == shown
    assert all(t and g in ("confirmed", "probable", "hypothesis", "lead") for t, _, g, _ in events)
    assert {b for *_, b in events} <= {1, 2, 3}
    assert ws["findings"] and all(f["text"] for f in ws["findings"])
    assert ws["narrative"] is not None and ws["narrative"]["sentences"]
    signal_rows = await sheet_rows("signals")
    assert len(signal_rows) == shown and all(r["_private"] for r in signal_rows)
    assert register["https://news.holdings.example/cio"]["how_retrieved"] == "Read in full"
    assert register["https://news.holdings.example/cio"]["supports"] == "The CIO appointment"


async def test_the_retry_of_a_failed_import_reads_the_zip_again_for_its_raw_pulls(
    tenant_a: UUID, tmp_path: Path
) -> None:
    """A workspace upload whose import failed is claimed again by the sweep's backfill. Given the
    document service's stores it reads the zip itself, so the raw pulls (full profiles) land
    too; without them only the deliverables do (UCB 2026-11-04)."""
    from documents.deliverables import backfill_imports

    account = _make_account(tenant_a, f"holdings-{uuid4().hex[:6]}.example")
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, workspace_zip.ZIP_NAME, _zip_with_twins(), account)
    doc = UUID(done["document_id"])
    assert await svc.process(tenant_a, doc) == "indexed"
    count = "SELECT count(*) FROM person_profiles WHERE account_id = %s"
    assert admin(count, account) == [(1,)]

    admin("DELETE FROM person_profiles WHERE account_id = %s", account)
    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", doc)
    await backfill_imports(datetime.now(UTC), limit=10)
    assert admin(count, account) == [(0,)]  # no stores given: deliverables only

    admin("UPDATE documents SET imported_at = NULL WHERE id = %s", doc)
    await backfill_imports(datetime.now(UTC), limit=10, payloads=svc.payloads, blobs=svc.blobs)
    assert admin(count, account) == [(1,)]
