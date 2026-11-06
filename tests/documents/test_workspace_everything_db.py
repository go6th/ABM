"""An uploaded workspace package comes back whole in the "Download everything" tables
(2026-11-05). SYNTHETIC data only: the owner-shaped package of ``workspace_zip_owner``.

The UCB bundle of 2026-11-04 lost most of the owner's workspace: closed postings and feed
records without a link, every platform outside the six lanes, 1,255 of 1,620 people, the former
employers, the leadership page, the news feed, the web searches, the confirmed stack and the
teams, the deliverables, and the research date. Each of those is one assertion here, against
the tables the repair stage builds from the imported rows, so none of them can come back
unnoticed.

GUARANTEE(test_the_everything_tables_carry_the_whole_workspace)
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from tests.conftest import _make_account
from tests.documents import workspace_zip_owner as OWNER
from tests.documents.world import service, upload

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _rows(table: Any, sheet: str) -> list[dict[str, str]]:
    found = next(s for s in table.sheets if s.name == sheet)
    return [
        r for r in found.rows if not str(r.get(found.columns[0], "")).startswith("Not collected")
    ]


async def _everything(tenant: UUID, account: UUID, svc: Any) -> tuple[Any, Any, Any]:
    import json

    from account_intelligence.replica import sheets as SH
    from agents.config import load_skill_config
    from exports.repair import pipeline as RP
    from exports.repair import readers as RD
    from exports.repair.profile import load_profile

    async with tenant_txn(tenant) as s:
        profile = await load_profile(
            s, account, "test-owner", datetime.now(UTC), load_skill_config().scope
        )
        inp = await RD.load_input(
            s,
            profile,
            account,
            "test-owner",
            payload=svc.payloads.get,
            tracker_labels=json.loads(SH.tracker_labels()),
        )
    return profile, inp, RP.repair(profile, inp)


async def test_the_everything_tables_carry_the_whole_workspace(
    tenant_a: UUID, tmp_path: Path
) -> None:
    # the account's own domain: the package names it, so nothing is held
    account = _make_account(tenant_a, OWNER.WORKSPACE_DOMAIN)
    svc = service(tmp_path)
    done = await upload(svc, tenant_a, OWNER.ZIP_NAME, OWNER.workspace_zip_bytes(), account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant_a, document) == "indexed"
    profile, inp, result = await _everything(tenant_a, account, svc)
    tables = result.tables

    # the window ends on the research date, not on the upload day
    assert profile.run_date == date(2026, 10, 24)
    assert profile.window_to == date(2026, 10, 24)

    # --- Job postings: every row of the owner's sheet, with its address and its number
    jobs = _rows(tables["jobs"], "All postings (unfiltered)")
    by_title = {r["Title"]: r for r in jobs}
    assert "Technical Lead CRM" in by_title, "a closed posting with a requisition but no link"
    closed = by_title["Technical Lead CRM"]
    assert closed["Status"] == "Closed"
    assert closed["Requisition ID"] == "93642"
    assert closed["Source URL"].startswith(f"https://{OWNER.CAREERS_HOST}/global/en/job/93642/")
    assert "Shift Leader Biomanufacturing" in by_title, "an archive record matched to the feed"
    assert by_title["Shift Leader Biomanufacturing"]["Source URL"].startswith(
        "https://www.linkedin.com/jobs/view/"
    )
    assert "IT CRM Technical Lead" in by_title, "the Hidden title roles sheet"
    assert all(r["Entity"] == profile.label for r in jobs), Counter(r["Entity"] for r in jobs)
    platform_rows = {r["Title"]: r for r in _rows(tables["jobs"], "Job postings")}
    assert "Data Analyst Trainee" in platform_rows, "a posting naming only Power BI and Qlik"
    assert platform_rows["Data Analyst Trainee"]["Class"] == "Outside the lanes, not classified"
    assert "Power BI" in platform_rows["Data Analyst Trainee"]["Platforms named"]
    omni = platform_rows["Digital Technology Lead, Omnichannel"]
    for name in ("Salesforce", "Veeva", "Microsoft Azure", "Power BI"):
        assert name in omni["Platforms named"], omni["Platforms named"]
    assert omni["Class"] == "Working tool"
    per_platform = tables["jobs"].stats["per_platform"]
    assert per_platform["Power BI"]["postings"] >= 2
    assert per_platform["SAP"]["postings"] >= 2
    assert tables["jobs"].stats["account_platform_postings"] >= 7
    assert "Not the account" not in {r["Entity"] for r in jobs}

    # --- People: everyone the owner listed, as written
    people = _rows(tables["people"], "People")
    assert len(people) == len(OWNER.PEOPLE_ROWS), [r["Name"] for r in people]
    by_name = {r["Name"]: r for r in people}
    assert "Dr. Usha Sample R BDS, MS" in by_name, "a name with credentials stays as reported"
    assert "Joseph (Robby) Placeholder" in by_name, "a nickname in brackets stays"
    assert "Noor Sample" in by_name, "a compliance title is not re-filtered"
    assert "Lee Junior" in by_name, "a junior is kept, marked No"
    assert by_name["Lee Junior"]["Meets seniority rule"] == "No"
    assert by_name["Anya Specimen"]["Meets seniority rule"] == "No (not current at Octavia)"
    assert by_name["Edwin Example"]["Level"] == "C-level, EVP and SVP"
    assert by_name["Edwin Example"]["Meets seniority rule"] == "Yes"
    assert by_name["Lee Junior"]["Level"] == "Lead, owner, specialist or developer"
    assert (
        by_name["Edwin Example"]["Former SI and consulting employers"] == "Accenture (2001 to 2009)"
    )
    assert (
        by_name["Dr. Usha Sample R BDS, MS"]["Former SI and consulting employers"]
        == "Deloitte (2014 to 2018)"
    )
    assert by_name["Edwin Example"]["LinkedIn"].startswith("https://")
    leaders = {r["Name"]: r for r in _rows(tables["people"], "Leadership")}
    assert {"Jean Fictional", "Sandrine Vermeersch"} <= set(leaders), leaders.keys()
    assert leaders["Jean Fictional"]["Status"] == "Current"
    assert leaders["Jean Fictional"]["Role"] == "Chief Executive Officer"

    # --- Tech stack and teams: the ranking, the confirmed stack and the teams of the package
    ranking = {r["Platform"]: r for r in _rows(tables["tech"], "Platform Ranking")}
    assert {"SAP", "Power BI", "Salesforce"} <= set(ranking), ranking.keys()
    stack = {r["Technology"]: r for r in _rows(tables["tech"], "Confirmed stack")}
    assert "SAP" in stack and "Power BI" in stack, stack.keys()
    assert stack["SAP"]["Business function"] == "Finance"
    assert int(stack["SAP"]["Postings naming it"]) >= 185
    assert "Snowflake" in stack, "a JSON:API detection resolved through its included technology"
    teams = {r["Team"]: r for r in _rows(tables["tech"], "Platform teams")}
    assert "Global Supply Chain team" in teams, teams.keys()
    assert teams["Global Supply Chain team"]["Postings"] == "11"
    assert "SAP" in teams["Global Supply Chain team"]["Platforms"]

    # --- News, searches, signals, sources
    news = _rows(tables["news"], "News")
    assert any("guidance" in r["Headline"].lower() for r in news), [r["Headline"] for r in news]
    guidance = next(r for r in news if "guidance" in r["Headline"].lower())
    assert guidance["Published"] == "2026-10-22"
    assert guidance["Publisher"].endswith("Newsroom")
    searches = _rows(tables["searches"], "Web searches")
    assert {r["Query"] for r in searches} == {
        "Octavia company overview leadership", "Octavia SAP S/4HANA integrator",
    }  # fmt: skip
    assert next(r for r in searches if "integrator" in r["Query"])["Pages returned (total)"] == "4"
    signals = {r["Signal"]: r for r in _rows(tables["signals"], "Signals")}
    assert any("campus" in k.lower() for k in signals), signals.keys()
    assert any("Fabric" in k for k in signals), signals.keys()
    assert all(r["Evidence (source codes)"] not in ("", "Not found") for r in signals.values())
    sources = _rows(tables["sources"], "Sources")
    assert len(sources) >= 5, [r["Title"] for r in sources]
    assert all(r["Publisher"] not in ("", "Not found") for r in sources)

    # --- Coverage: an item proven by its table is Done with the rows as evidence
    coverage = {r["Research item"]: r for r in _rows(tables["coverage"], "Coverage")}
    for item in ("Job postings, last 12 months, open and closed", "Technology detections",
                 "Company news and press releases"):  # fmt: skip
        assert coverage[item]["Status"] == "Done", (item, coverage[item])
        assert coverage[item]["Evidence (row counts / source codes)"].split()[0].isdigit()
    youtube = coverage["YouTube"]
    assert youtube["Status"] == "Done"
    assert "transcripts" in youtube["Evidence (row counts / source codes)"]

    # --- the deliverables of the package ship with the bundle, and the checks pass
    from account_intelligence.replica.downloads import _workspace_deliverables
    from documents import repo

    async with tenant_txn(tenant_a) as s:
        documents = await repo.list_for(s, account)

    class _Src:
        blobs = svc.blobs

    files = _workspace_deliverables(_Src(), documents)
    assert set(files) == {
        OWNER.JOBS_FILE, OWNER.PEOPLE_FILE, OWNER.TECH_FILE, OWNER.SUMMARY_FILE,
        OWNER.EVIDENCE_FILE,
    }  # fmt: skip
    failed = {c.name: c.detail for c in result.checks if not c.passed}
    assert not failed, failed
    assert (
        next(c for c in result.checks if c.name == "entity_resolves_account").numbers["account"]
        >= 8
    )
