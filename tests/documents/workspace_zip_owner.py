"""A SYNTHETIC workspace zip in the shape the owner's own packages have (the UCB package of
2026-11-05 was the model; every name, number and address here is made up).

What the UCB "everything" bundle got wrong, and this package carries so the tests can prove it
stays right:

* the package names its own company (``data/company_record.json``, ``RESTORE.md``), which is
  NOT the account's domain in the gate tests: ``octavia-bio.example`` against an account
  ``octavia-us.example``;
* the Jobs sheet links only the postings seen live; a closed posting with a requisition carries
  no link, an archive record carries neither a requisition nor a link but matches the feed in
  ``data/jobs_12mo.json``; some rows name only Power BI, Snowflake or SAS (outside the six
  lanes); a ``Hidden title roles read`` sheet lists roles read in full;
* the People sheet keeps everyone (juniors too) with the owner's numbered levels, the
  "Meets seniority rule" verdict (including ``No (not current at Octavia)``), former SI and
  consulting employers with years, a name with credentials and a nickname in brackets;
* ``data/leadership.json`` names the executive committee; ``data/news_events.json`` holds feed
  events; ``data/web_searches.json`` the searches; ``data/techstack_confirmed.json`` and
  ``data/teams.json`` the stack and the teams; ``data/releases`` captured newsroom pages;
* the Account Summary carries a coverage tracker, a signals table (with an "other"-looking
  signal) and the source register.

Run directly to write it somewhere for inspection:
``python -m tests.documents.workspace_zip_owner /tmp/Octavia_workspace.zip``.
"""

from __future__ import annotations

import functools
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

ACCOUNT_NAME = "Octavia Bio"
WORKSPACE_DOMAIN = "octavia-bio.example"  # what the package names
OTHER_ACCOUNT_DOMAIN = "octavia-us.example"  # the account the gate tests upload it to
CAREERS_HOST = "careers.octavia-bio.example"
RESEARCH_DATE = "2026-10-24"
ZIP_NAME = "Octavia_workspace.zip"
FOLDER = "Octavia_workspace"
JOBS_FILE = "Octavia_Job_Postings.xlsx"
PEOPLE_FILE = "Octavia_People_List.xlsx"
TECH_FILE = "Octavia_Tech_Stack_and_Teams.xlsx"
SUMMARY_FILE = "Octavia_Account_Summary.docx"
EVIDENCE_FILE = "Octavia_Sources_and_Evidence.docx"

JOBS_COLUMNS = [
    "Requisition", "Title", "Platforms named", "Class", "Status", "First seen", "Last seen",
    "Location", "Seniority", "What the posting says (read)", "Platform sentences",
    "Full description", "Link",
]  # fmt: skip


def _posting_url(req: str, title: str) -> str:
    slug = "-".join(w for w in title.replace(",", " ").split())
    return f"https://{CAREERS_HOST}/global/en/job/{req}/{slug}"


# (requisition, title, platforms, class, status, first seen, last seen, location, seniority,
#  summary, sentences, description, linked)
JOB_ROWS: list[tuple[Any, ...]] = [
    ("94001", "Head of Site Supply Chain", "SAP", "Working tool", "Open", "2026-10-21",
     "2026-10-23", "Bulle, Switzerland", "head", "Site supply chain head; SAP APO daily.",
     "SAP: You will ensure the effective use of SAP APO.", "Make your mark. SAP APO daily.",
     True),
    ("94002", "Digital Technology Lead, Omnichannel",
     "Salesforce, Veeva, Microsoft Azure, Power BI",
     "Working tool", "Open", "2026-10-01", "2026-10-24", "Atlanta, Georgia, United States",
     "mid_senior", "Leads omnichannel analytics on Salesforce Marketing Cloud and Veeva CRM.",
     "Salesforce: Experience with Salesforce Marketing Cloud. || Veeva: Veeva CRM data.",
     "Omnichannel lead. Salesforce Marketing Cloud, Veeva CRM, Azure and Power BI reporting.",
     True),
    ("94003", "Asset Management Excellence Lead", "ServiceNow", "Admin and owner role", "Open",
     "2026-09-24", "2026-10-24", "Braine-l'Alleud, Belgium", "mid_senior",
     "Newly created role to implement ServiceNow for IT asset management.",
     "ServiceNow: contribute to the implementation of ServiceNow.",
     "Newly created role. Contribute to the implementation of ServiceNow ITAM.", True),
    ("93642", "Technical Lead CRM", "Salesforce", "Build role", "Closed", "2026-07-28",
     "2026-08-12", "Atlanta, Georgia, United States", "mid_senior",
     "Technical partner for Salesforce Health Cloud.",
     "Salesforce: You will serve as the technical lead for Salesforce Health Cloud.",
     "Technical Lead CRM. Salesforce Health Cloud build.", False),  # closed: no link
    ("92529", "Principal Data Governance Architect", "Snowflake, Microsoft Azure, Collibra",
     "Outside the lanes, not classified", "Open", "2026-03-04", "2026-10-24",
     "Atlanta, Georgia, United States", "", "", "Snowflake: Experience with Snowflake.",
     "Data governance architect. Snowflake and Azure data foundation.", True),
    ("", "Shift Leader Biomanufacturing", "SAP", "Working tool", "Closed", "2026-08-17",
     "2026-10-08", "Belgium, Western Europe, Europe", "manager", "",
     "SAP: Encoder les notifications dans SAP.", "Shift leader. Encoder dans SAP.", False),
    ("", "Data Analyst Trainee", "Power BI, Qlik", "Outside the lanes, not classified", "Closed",
     "2026-06-21", "2026-06-26", "Bulle, Fribourg, Switzerland", "junior", "",
     "Power BI: Mise en place de dashboards Power BI.", "Trainee. Power BI and Qlik dashboards.",
     False),
    ("93688", "Senior Principal Programmer", "Microsoft Azure, SAS",
     "Outside the lanes, not classified", "Closed", "2026-07-31", "2026-08-03",
     "Mettmann, Germany", "mid_senior", "", "SAS: Proficiency in SAS.",
     "Statistical programmer. SAS and Azure.", False),
    # an archive record with no requisition, no link and no feed record (three such rows in the
    # UCB package): kept as written, without an address (migration 0111, exact copy 2026-11-05)
    ("", "Technicien de maintenance, biologie (nuit, week-end)", "",
     "Outside the lanes, not classified", "Closed", "2026-03-02", "2026-03-30",
     "Braine-l'Alleud, Belgium", "", "", "",
     "Maintenance technician. Night and weekend shifts; no platform named.", False),
]  # fmt: skip

HIDDEN_ROWS = [
    ("94217", "IT CRM Technical Lead", "Brussels", "Open",
     "Leads the architecture of the CRM platform; names no platform in the title.", True),
]  # fmt: skip

# feed records: the archive rows above match these by title, location and first seen
FEED_JOBS = [
    {
        "id": "f-1",
        "title": "Shift Leader Biomanufacturing",
        "url": "https://www.linkedin.com/jobs/view/4411111111/",
        "status": "closed",
        "first_seen_at": "2026-08-17T08:00:00Z",
        "last_seen_at": "2026-10-08T08:00:00Z",
        "location": "Belgium, Western Europe, Europe",
        "seniority": "manager",
        "description": "Shift leader. Encoder dans SAP.",
    },
    {
        "id": "f-2",
        "title": "Data Analyst Trainee",
        "url": "https://www.linkedin.com/jobs/view/4422222222/",
        "status": "closed",
        "first_seen_at": "2026-06-21T08:00:00Z",
        "last_seen_at": "2026-06-26T08:00:00Z",
        "location": "Bulle, Fribourg, Switzerland",
        "seniority": "junior",
        "description": "Trainee. Power BI and Qlik dashboards.",
    },
]

PEOPLE_COLUMNS = [
    "No.", "Name", "Title as reported", "Level", "Meets seniority rule", "Lane", "Priority",
    "Former SI and consulting employers (firm and years)", "Joined Octavia", "Current role since",
    "Location", "Platform and method certifications", "LinkedIn activity, last 90 days",
    "Found by", "LinkedIn", "Second profile", "Note",
]  # fmt: skip

PEOPLE_ROWS: list[tuple[Any, ...]] = [
    (1, "Edwin Example", "Chief Digital Technology Officer", "1 C-level and SVP", "Yes",
     "Digital Technology leadership and other", "A", "Accenture (2001 to 2009)", "2019-01",
     "2022-05", "Brussels, Belgium", "", "3 posts", "Title search (platform and leadership titles)",
     "https://www.linkedin.com/in/edwin-example", "", ""),
    (2, "Dr. Usha Sample R BDS, MS", "Senior Manager, US Commercial Data Operations",
     "7 Senior Manager", "Yes", "Analytics, data and AI", "A", "Deloitte (2014 to 2018)",
     "2026-08", "2026-08", "Atlanta, Georgia, United States", "", "none",
     "Title search (platform and leadership titles)", "https://www.linkedin.com/in/usha-sample",
     "", ""),
    (3, "Joseph (Robby) Placeholder", "Head of Data Management Operations, US Commercial",
     "6 Head", "Yes", "Analytics, data and AI", "A", "", "2021-03", "2024-01",
     "Atlanta, Georgia, United States", "", "1 post", "Named in research",
     "https://www.linkedin.com/in/robby-placeholder", "", "Named in the Microsoft story."),
    (4, "Anya Specimen", "Senior Manager Clinical Systems", "7 Senior Manager",
     "No (not current at Octavia)", "Veeva and R&D, quality, medical systems", "C", "",
     "2018-02", "2020-06", "Slough, United Kingdom", "", "none",
     "Title search (platform and leadership titles)", "https://www.linkedin.com/in/anya-specimen",
     "", "Profile shows no current Octavia role."),
    (5, "Noor Sample", "Compliance & Training Manager", "8 Manager", "No",
     "Business role (not a platform role)", "C", "", "2015-09", "2015-09", "Brussels, Belgium",
     "", "none", "Title search (platform and leadership titles)",
     "https://www.linkedin.com/in/noor-sample", "", ""),
    (6, "Lee Junior", "Data Engineer", "9 Lead, owner, specialist or developer", "No",
     "Analytics, data and AI", "C", "", "2024-01", "2024-01", "Brussels, Belgium",
     "Azure", "none", "Title search (platform and leadership titles)",
     "https://www.linkedin.com/in/lee-junior", "", ""),
]  # fmt: skip


def _workbook(
    sheets: dict[str, list[list[Any]]], links: dict[tuple[str, int], str] | None = None
) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for row in rows:
            ws.append(row)
        for (sheet, col), url_of_row in (links or {}).items():
            if sheet != name:
                continue
            for r in range(2, ws.max_row + 1):
                target = url_of_row[r] if isinstance(url_of_row, dict) else None  # type: ignore[index]
                if target:
                    cell = ws.cell(row=r, column=col)
                    cell.hyperlink = target
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def jobs_xlsx() -> bytes:
    """The Jobs workbook: Summary (title block), Jobs, Hidden title roles read."""
    from openpyxl import Workbook

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(["Octavia platform job postings, last 12 months"])
    summary.append(
        ["Every Octavia posting first seen between October 24, 2025 and October 24, 2026."]
    )
    summary.append(["Platform (named anywhere)", "Total", "Closed", "Open"])
    summary.append(["SAP", 2, 1, 1])
    jobs = wb.create_sheet("Jobs")
    jobs.append(JOBS_COLUMNS)
    link_col = JOBS_COLUMNS.index("Link") + 1
    for i, row in enumerate(JOB_ROWS, start=2):
        req, title, platforms, klass, status, first, last, loc, sen, summ, sent, desc, linked = row
        jobs.append([req, title, platforms, klass, status, first, last, loc, sen, summ, sent, desc,
                     "Open posting" if linked else "Archive record"])  # fmt: skip
        if linked:
            jobs.cell(row=i, column=link_col).hyperlink = _posting_url(req, title)
    hidden = wb.create_sheet("Hidden title roles read")
    hidden.append(
        [
            "Requisition",
            "Title",
            "Location",
            "Status",
            "What the posting says (read in full)",
            "Link",
        ]
    )
    for i, (req, title, loc, status, what, linked) in enumerate(HIDDEN_ROWS, start=2):
        hidden.append([req, title, loc, status, what, "Open posting"])
        if linked:
            hidden.cell(row=i, column=6).hyperlink = _posting_url(req, title)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def people_xlsx() -> bytes:
    """The People workbook: Summary (title block), People with a LinkedIn hyperlink per row."""
    from openpyxl import Workbook

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(["Octavia people list, deduplicated"])
    summary.append(["Level", "People"])
    summary.append(["1 C-level and SVP", 1])
    people = wb.create_sheet("People")
    people.append(PEOPLE_COLUMNS)
    link_col = PEOPLE_COLUMNS.index("LinkedIn") + 1
    for i, row in enumerate(PEOPLE_ROWS, start=2):
        values = list(row)
        url = values[link_col - 1]
        values[link_col - 1] = "Open profile"
        people.append(values)
        people.cell(row=i, column=link_col).hyperlink = url
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def tech_xlsx() -> bytes:
    """The Tech workbook in the owner's layout: Platform Ranking, Confirmed stack, Platform
    teams, Technology detections, each with a title block above its header."""
    return _workbook(
        {
            "Platform Ranking": [
                ["Octavia: platforms seen in job postings, ranked A, B, C"],
                ["Postings in the last 12 months that name a platform", "", "", "", 8],
                [
                    "Rank",
                    "Platform",
                    "Category",
                    "Lane",
                    "Postings naming it",
                    "Share of base",
                    "Open now",
                    "Last seen",
                    "In confirmed stack",
                    "Matches checked by reading",
                ],
                ["A", "SAP", "ERP", "SAP", 2, 0.25, 1, "2026-10-24", "Yes", "Yes"],
                [
                    "B",
                    "Power BI",
                    "Analytics and BI",
                    "Data and AI",
                    2,
                    0.25,
                    1,
                    "2026-10-24",
                    "Yes",
                    "Yes",
                ],
                ["C", "Salesforce", "CRM", "Salesforce", 2, 0.25, 1, "2026-10-24", "Yes", "Yes"],
            ],  # fmt: skip
            "Confirmed stack": [
                ["Octavia technologies confirmed as used, by business function"],
                ["Business function", "Category", "Technology", "Postings describing it as used"],
                ["Finance", "ERP", "SAP", 185],
                ["Data & Analytics", "Business Intelligence", "Microsoft Power BI", 67],
                ["Sales", "CRM", "Salesforce", 15],
            ],
            "Platform teams": [
                ["Octavia teams whose postings name a lane platform (top 25 of 40 by postings)"],
                [
                    "Team",
                    "Lane technology (postings)",
                    "Jobs count",
                    "First activity",
                    "Last activity",
                    "Team technologies (postings)",
                    "Team locations (postings)",
                    "Job functions (postings)",
                ],
                [
                    "Global Supply Chain team",
                    "SAP (35)",
                    11,
                    "Not pulled",
                    "2026-04-17",
                    "SAP (35), OMP (7)",
                    "Kingdom of Belgium (64)",
                    "Strategy & Operations (60)",
                ],
                [
                    "Insight to Impact team",
                    "Salesforce (6), SAP (6)",
                    8,
                    "Not pulled",
                    "2026-10-03",
                    "Microsoft Power BI (15), Qlik (9)",
                    "Kingdom of Belgium (13)",
                    "Analytics (25)",
                ],
            ],  # fmt: skip
        }
    )


def summary_docx() -> bytes:
    """The Account Summary: a news table, a signals table (with an "other" signal), a key
    people table, the coverage tracker and the source register as the owner's files carry them."""
    from docx import Document

    doc = Document()

    def table(rows: list[list[str]]) -> None:
        t = doc.add_table(rows=len(rows), cols=len(rows[0]))
        for r, cells in enumerate(rows):
            for c, value in enumerate(cells):
                t.cell(r, c).text = value

    doc.add_heading("Octavia Account Summary", level=0)
    doc.add_paragraph("Account research, phase 2. Research date October 24, 2026.")
    doc.add_heading("2. Recent news (newest first)", level=1)
    table(
        [
            ["Date", "What happened", "Read depth", "Source", "Why it matters"],
            ["22 Oct 2026", "Guidance upgraded", "Read", "S2", "Budget headroom"],
        ]
    )
    doc.add_heading("4. Signals", level=1)
    doc.add_heading("Tier 1, budget proximate", level=2)
    table(
        [
            ["Signal", "Grade", "Lanes", "Date", "Sources"],
            ["Georgia greenfield biologics campus", "Confirmed", "SAP", "24 Mar 2026", "S3"],
            [
                "Fabric implemented as the enterprise analytics platform",
                "Probable",
                "Data and AI",
                "Jun 2026",
                "S4",
            ],
            [
                "New Chief Commercial Officer from January 2026",
                "Confirmed",
                "Salesforce",
                "6 Nov 2025",
                "S5",
            ],
        ]  # fmt: skip
    )
    doc.add_heading("8. Coverage", level=1)
    table(
        [
            ["Source type", "Status", "Priority", "Needs", "What was done", "What remains"],
            [
                "Company news and press releases",
                "Done",
                "High",
                "",
                "14 newsroom pages read, 70 releases captured.",
                "",
            ],
            [
                "Job postings, last 12 months, open and closed",
                "Done",
                "High",
                "",
                "748 feed records plus 19 live board records.",
                "",
            ],
            ["Technology detections", "Done", "High", "", "201 detections on the site.", ""],
            ["YouTube", "Done", "Medium", "", "4 YouTube queries and 8 transcripts read.", ""],
            ["Podcasts", "Partial", "Medium", "", "3 podcast queries.", "Two episodes unread."],
        ]  # fmt: skip
    )
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def evidence_docx() -> bytes:
    """The Sources and Evidence companion: Part A, the source register (ID, Source, Type, Date,
    How retrieved, What it supports, Domain)."""
    from docx import Document

    doc = Document()
    doc.add_heading("Octavia Sources and Evidence", level=0)
    doc.add_heading("Part A. Source register", level=1)
    rows = [
        ["ID", "Source", "Type", "Date", "How retrieved", "What it supports", "Domain"],
        ["S1", "Octavia Executive Committee page", "Company site", "2026-10-24", "Page read",
         "Leadership", f"https://www.{WORKSPACE_DOMAIN}/about/management"],
        ["S2", "Octavia release: guidance upgrade", "Press release", "2026-10-22", "Page read",
         "Financials", f"https://www.{WORKSPACE_DOMAIN}/newsroom/press-releases/guidance-upgrade"],
        ["S3", "Octavia selects Georgia for a biologics facility", "Press release", "2026-03-24",
         "Page read", "Investment", f"https://www.{WORKSPACE_DOMAIN}/newsroom/press-releases/georgia"],
        ["S4", "Microsoft customer story: Octavia and Fabric", "Vendor site", "2025-11-10",
         "Page read", "Analytics platform", "https://www.microsoft.com/customers/octavia"],
        ["S5", "Octavia executive leadership rotation", "Press release", "2025-11-06", "Page read",
         "Leadership", f"https://www.{WORKSPACE_DOMAIN}/newsroom/press-releases/rotation"],
    ]  # fmt: skip
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    for r, cells in enumerate(rows):
        for c, value in enumerate(cells):
            if r > 0 and c == 1:
                # the Source cell is a hyperlink to the page, as the owner's files carry it
                para = t.cell(r, c).paragraphs[0]
                r_id = para.part.relate_to(cells[6], RT.HYPERLINK, is_external=True)
                link = OxmlElement("w:hyperlink")
                link.set(qn("r:id"), r_id)
                run = OxmlElement("w:r")
                text = OxmlElement("w:t")
                text.text = value
                run.append(text)
                link.append(run)
                para._p.append(link)
            else:
                t.cell(r, c).text = value
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def raw_members() -> dict[str, bytes]:
    """The raw pulls, captured pages and notes."""
    base = f"{FOLDER}/data"
    return {
        f"{FOLDER}/RESTORE.md": (
            f"# {ACCOUNT_NAME} workspace ({WORKSPACE_DOMAIN}), October 24, 2026\n\n"
            "Account research by 6th agents. Synthetic.\n"
        ).encode(),
        f"{base}/company_record.json": json.dumps(
            {
                "source": "6th agents",
                "domain": WORKSPACE_DOMAIN,
                "company_name": ACCOUNT_NAME,
                "ticker": "EBR:OCT",
                "hq": "Brussels, Belgium",
                "pulled": RESEARCH_DATE,
            }
        ).encode(),
        f"{base}/org_resolve.json": json.dumps(
            {
                "source": "6th agents",
                "params": {"name": ACCOUNT_NAME, "url": WORKSPACE_DOMAIN},
                "name": ACCOUNT_NAME,
                "employee_count": 11761,
            }
        ).encode(),  # fmt: skip
        f"{base}/live_postings_index.json": json.dumps(
            [
                {"req": r[0], "title": r[1], "url": _posting_url(r[0], r[1]), "in_feed": True}
                for r in JOB_ROWS
                if r[0] and r[-1]
            ]
        ).encode(),  # fmt: skip
        f"{base}/jobs_12mo.json": json.dumps(
            {
                "source": "6th agents",
                "domain": WORKSPACE_DOMAIN,
                "count_reported": 2,
                "count_saved": 2,
                "jobs": FEED_JOBS,
            }
        ).encode(),  # fmt: skip
        f"{base}/leadership.json": json.dumps(
            {
                "source": "6th agents",
                "url": f"https://www.{WORKSPACE_DOMAIN}/about/management",
                "pulled": RESEARCH_DATE,
                "executive_committee": [
                    {"name": "Jean Fictional", "title": "Chief Executive Officer"},
                    {"name": "Sandrine Vermeersch", "title": "Chief Financial Officer"},
                ],
                "note": "No CIO listed on the Executive Committee page.",
            }
        ).encode(),
        f"{base}/techstack_detections.json": json.dumps(
            {
                "data": [
                    {
                        "id": "det-1",
                        "type": "technology_detection",
                        "attributes": {
                            "first_seen_at": "2026-10-10T08:00:00Z",
                            "last_seen_at": "2026-10-24T08:00:00Z",
                            "behind_firewall": True,
                            "score": 0.9,
                            "source_count": 3,
                            "location_data": [
                                {"city": None, "state": "Georgia", "country": "United States"}
                            ],
                        },
                        "relationships": {
                            "technology": {"data": {"id": "tech-1", "type": "technology"}},
                            "seen_on_job_openings": {"data": [{"id": "j1", "type": "job_opening"}]},
                            "seen_on_subpages": {"data": []},
                        },
                    }
                ],
                "included": [
                    {"id": "tech-1", "type": "technology", "attributes": {"name": "Snowflake"}}
                ],
                "meta": {},
            }
        ).encode(),  # fmt: skip
        f"{base}/news_events.json": json.dumps(
            {
                "data": [
                    {
                        "id": "ev-1",
                        "type": "news_event",
                        "attributes": {
                            "headline": "Octavia upgrades its 2026 guidance",
                            "summary": "Guidance up after a strong quarter.",
                            "category": "has_valuation",
                            "found_at": "2026-10-22T08:00:00Z",
                            "effective_date": "2026-10-22",
                            "confidence": 0.9,
                            "article_sentence": "Octavia raised its guidance.",
                            "location_data": [],
                        },
                        "relationships": {
                            "company1": {"data": {"id": "co-1", "type": "company"}},
                            "most_relevant_source": {
                                "data": {"id": "art-1", "type": "news_article"}
                            },
                        },
                    }
                ],
                "included": [
                    {"id": "co-1", "type": "company", "attributes": {"domain": WORKSPACE_DOMAIN}},
                    {
                        "id": "art-1",
                        "type": "news_article",
                        "attributes": {
                            "title": "Octavia upgrades its 2026 guidance",
                            "url": f"https://www.{WORKSPACE_DOMAIN}/newsroom/press-releases/guidance-upgrade",
                            "published_at": "2026-10-22T07:00:00Z",
                        },
                    },
                ],
                "meta": {},
            }
        ).encode(),  # fmt: skip
        f"{base}/web_searches.json": json.dumps(
            [
                {
                    "id": 1,
                    "group": "A",
                    "query": "Octavia company overview leadership",
                    "result_count": 10,
                    "top_results": [
                        {"title": "About Octavia", "url": f"https://www.{WORKSPACE_DOMAIN}/about"},
                        {
                            "title": "Octavia - Wikipedia",
                            "url": "https://en.wikipedia.org/wiki/Octavia",
                        },
                    ],
                },
                {
                    "id": 2,
                    "group": "B",
                    "query": "Octavia SAP S/4HANA integrator",
                    "result_count": 4,
                    "top_results": [
                        {"title": "Octavia S/4HANA", "url": "https://www.talan.example/octavia"}
                    ],
                },
            ]
        ).encode(),  # fmt: skip
        f"{base}/techstack_confirmed.json": json.dumps(
            {
                "source": "6th agents",
                "stack": {
                    "Finance": {"ERP": [{"name": "SAP", "job_post_used_count": 185}]},
                    "Data & Analytics": {
                        "Business Intelligence": [
                            {"name": "Microsoft Power BI", "job_post_used_count": 67}
                        ]
                    },
                },
            }
        ).encode(),
        f"{base}/teams.json": json.dumps(
            {
                "source": "6th agents",
                "total_matching": 40,
                "teams": [
                    {
                        "team_id": 1,
                        "name": "Global Supply Chain team",
                        "attributes": {
                            "jobs_count": 11,
                            "technology_list": [{"name": "SAP", "used": True, "jobs_count": 35}],
                            "location_list": [{"name": "Kingdom of Belgium", "jobs_count": 64}],
                            "function_list": [{"name": "Strategy & Operations", "jobs_count": 60}],
                            "last_activity": "2026-04-17",
                        },
                    }
                ],
            }
        ).encode(),
        f"{base}/releases/2026-10-22_guidance-upgrade.md": (
            f"title: Octavia upgrades its 2026 guidance\n"
            f"url: https://www.{WORKSPACE_DOMAIN}/newsroom/press-releases/guidance-upgrade\n"
            f"date: 2026-10-22\nsource: 6th agents\n\n"
            "# Octavia upgrades its 2026 guidance\n\nStrong quarter. Guidance raised.\n"
        ).encode(),
        f"{base}/releases/2026-10-24_live_94001_Head-of-Site-Supply-Chain.md": (
            "title: Head of Site Supply Chain\n"
            f"url: {_posting_url('94001', 'Head of Site Supply Chain')}\n"
            "date: 2026-10-24\nsource: 6th agents\n\nHead of Site Supply Chain\n\nSAP APO daily.\n"
        ).encode(),
        f"{FOLDER}/notes/pull_log.md": (
            b"# Octavia phase 1 pull log\n\nRun date: 2026-10-24.\n\n## Calls\n\n"
            b"| Step | Tool | Parameters as sent | Result count | Output file |\n"
            b"|---|---|---|---|---|\n"
            b"| 2.1 | open web search | queries: Octavia overview | 10 | data/web_searches.json |\n"
        ),
        f"{FOLDER}/notes/sense_check.md": b"# Sense check\n\nThe entity is right.\n",
        f"{FOLDER}/build/build_people.py": b"# synthetic placeholder\nprint('clean')\n",
    }


@functools.cache
def workspace_zip_bytes() -> bytes:
    """The owner-shaped synthetic workspace zip (built once per process)."""
    members: dict[str, bytes] = {
        f"{FOLDER}/deliverables/{JOBS_FILE}": jobs_xlsx(),
        f"{FOLDER}/deliverables/{PEOPLE_FILE}": people_xlsx(),
        f"{FOLDER}/deliverables/{TECH_FILE}": tech_xlsx(),
        f"{FOLDER}/deliverables/{SUMMARY_FILE}": summary_docx(),
        f"{FOLDER}/deliverables/{EVIDENCE_FILE}": evidence_docx(),
        **raw_members(),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 24, 10, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out.getvalue()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ZIP_NAME)
    target.write_bytes(workspace_zip_bytes())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
