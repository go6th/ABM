"""A SYNTHETIC account workspace zip for the tabular tests and the live tabular eval.

The zip has the structure of the workspace package a research delivers (a folder named for the
account holding ``RESTORE.md``, ``deliverables/`` with the Job Postings, People List and Tech
Stack and Teams workbooks, ``notes/``, ``data/`` and ``build/``), for the fictional account
"Synthetic Holdings" (``holdings.example``). Everything is made up and deterministic: the Jobs
sheet is ``tests.documents.fixtures.holdings_job_postings_xlsx`` (644 rows, 13 columns), the
people are invented names, the Teams sheet and its JSON twin are 40-row decoys built from the
postings (the live eval's "wrong table" trap). No real account's data is in the repository
(owner decision 2026-10-30; the former real fixture is parked in
``unwanted-things/real-account-fixtures/``).

Run directly to write the zip somewhere for inspection:
``python -m tests.documents.workspace_zip /tmp/Holdings_workspace.zip``.
"""

from __future__ import annotations

import functools
import io
import json
import sys
import zipfile
from pathlib import Path

from tests.documents import fixtures

ACCOUNT_NAME = "Synthetic Holdings"
ACCOUNT_DOMAIN = "holdings.example"
ZIP_NAME = "Holdings_workspace.zip"
FOLDER = "holdings"
JOBS_FILE = "Holdings_Job_Postings.xlsx"
PEOPLE_FILE = "Holdings_People_List.xlsx"
TECH_FILE = "Holdings_Tech_Stack_and_Teams.xlsx"
JOBS_TABLE = f"{JOBS_FILE}:Jobs"
# The known truth of the Jobs sheet (derived from ``fixtures.HOLDINGS_SALESFORCE_RECENT``):
# 40 postings name Salesforce, all Closed; none is open and first seen on or after 2026-03-01;
# relaxing the status alone gives 20 rows, 14 unique postings (12 requisitions plus the two
# keyless ones that dedupe on title and location).
SALESFORCE_ROWS = 40
RELAXED_ROWS = 20
RELAXED_UNIQUE = 14
OWNER_IDS = {
    req
    for req, _title, klass, _genuine, _copies in fixtures.HOLDINGS_SALESFORCE_RECENT
    if req and klass in fixtures.OWNER_CLASSES
}
FALSE_POSITIVES = set(fixtures.HOLDINGS_FALSE_POSITIVES)

_PEOPLE = [
    ("Avery Example", "VP, Commercial Systems", "Salesforce", "Director and above"),
    ("Blake Sample", "Director, CRM Platforms", "Salesforce", "Director and above"),
    ("Casey Placeholder", "Head of Digital Engagement", "Veeva", "Director and above"),
    ("Devon Specimen", "Senior Director, Data Platforms", "Snowflake", "Director and above"),
    ("Emery Fictional", "Director, Enterprise Applications", "SAP", "Director and above"),
]


def _workbook(sheets: dict[str, list[list[object]]]) -> bytes:
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


def _teams() -> list[list[object]]:
    """40 team rows that look like the postings but are not the table the questions are about."""
    rows: list[list[object]] = [["Team", "Platform", "Postings in the last 12 months"]]
    for i in range(40):
        rows.append([f"Team {i + 1}", "Salesforce", 1 + i % 4])
    return rows


@functools.cache
def workspace_zip_bytes() -> bytes:
    """The synthetic workspace zip, byte for byte the same on every call (built once per process:
    a workbook saved by openpyxl carries its creation time)."""
    teams = _teams()
    members: dict[str, bytes] = {
        f"{FOLDER}/RESTORE.md": (
            f"# {ACCOUNT_NAME} workspace\n\nA synthetic workspace used by the test suite. "
            "Deliverables are in `deliverables/`, the raw pulls in `data/`.\n"
        ).encode(),
        f"{FOLDER}/notes/handoff.md": (
            b"# Hand-off\n\nSynthetic notes. The Jobs sheet holds the postings; the Teams sheet "
            b"is a summary by team and is not the postings.\n"
        ),
        f"{FOLDER}/deliverables/{JOBS_FILE}": fixtures.holdings_job_postings_xlsx(),
        f"{FOLDER}/deliverables/{PEOPLE_FILE}": _workbook(
            {
                "People": [["Name", "Title", "Platform", "Tier"], *[list(p) for p in _PEOPLE]],
            }
        ),
        f"{FOLDER}/deliverables/{TECH_FILE}": _workbook(
            {
                "Stack": [["Platform", "Confirmed used"], ["Salesforce", "Yes"], ["Veeva", "Yes"]],
                "Teams": teams,
            }
        ),
        f"{FOLDER}/data/holdings_teams_platform_12mo.json": json.dumps(
            [{"team": r[0], "platform": r[1], "postings": r[2]} for r in teams[1:]], indent=1
        ).encode(),
        f"{FOLDER}/build/third_party_scan.py": b"# synthetic placeholder\nprint('clean')\n",
    }
    out = io.BytesIO()
    # fixed timestamps: the same bytes (and the same content hash) on every call
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 24, 10, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out.getvalue()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ZIP_NAME)
    target.write_bytes(workspace_zip_bytes())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
