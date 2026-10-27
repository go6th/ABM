"""Output safety (spec 9.9, P16): only in-scope people by name, a Summary of counts by status and
reason, no provider code, call reference, credit figure or contact detail, and the client-output
scanner passes on the written files."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from people.export import build_export
from people.models import Classification, Profile
from people.pipeline import PipelineResult
from tests.fakes.people import Corpus
from tests.people.kit import Kit

ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / ".claude/skills/account-intelligence-platform/scripts/scan_client_output.py"


async def _result() -> PipelineResult:
    return await Kit(Corpus()).run()


def _write(result: PipelineResult, folder: Path, erased: frozenset[str] = frozenset()) -> Path:
    assert result.counts is not None
    export = build_export(
        result.people, result.counts, result.count_sentences, result.source_codes, erased
    )
    out = folder / "people_list.csv"
    with out.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Name", "Title", "Level", "Lanes", "Priority", "Profile", "Source"])
        for row in export.rows:
            writer.writerow(list(row.__dict__.values()))
    with (folder / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Status", "Reason", "Count"])
        writer.writerows(export.summary)
        for line in export.sentences:
            writer.writerow([line, "", ""])
    return folder


@pytest.mark.asyncio
async def test_export_rows_and_summary(tmp_path: Path) -> None:
    result = await _result()
    assert result.counts is not None
    export = build_export(result.people, result.counts, result.count_sentences, result.source_codes)
    in_scope = [p for p in result.people if p.status == "in_scope" and not p.internal_only]
    assert {r.profile_url for r in export.rows} == {p.profile_url for p in in_scope}
    assert all(r.source_code.startswith("S-") for r in export.rows)
    names_out = {p.name for p in result.people if p.status != "in_scope"} - {
        p.name for p in in_scope
    }
    cells = {c for r in export.rows for c in r.__dict__.values()} | {
        c for s in export.summary for c in map(str, s)
    }
    assert not names_out & cells  # no excluded, review, unresolved or merged name anywhere
    assert ("excluded", "security", 1) in export.summary
    assert ("no profile link yet", "", 3) in export.summary
    text = " ".join(str(c) for c in cells) + " ".join(export.sentences)
    for forbidden in ("p_people", "p_org", "raw/", "credit"):
        assert forbidden not in text
    for p in result.people:  # no call reference anywhere
        if p.call_ref:
            assert p.call_ref not in text


@pytest.mark.asyncio
async def test_erased_people_never_reach_an_export(tmp_path: Path) -> None:
    result = await _result()
    assert result.counts is not None
    jane = next(
        p
        for p in result.people
        if p.status == "in_scope" and (p.profile_url or "").endswith("/jane-doe")
    )
    export = build_export(
        result.people, result.counts, (), result.source_codes, frozenset({jane.person_key})
    )
    assert jane.profile_url not in {r.profile_url for r in export.rows}


@pytest.mark.asyncio
async def test_scanner_passes_on_the_written_export(tmp_path: Path) -> None:
    folder = _write(await _result(), tmp_path)
    run = subprocess.run(
        [
            sys.executable,
            str(SCANNER),
            str(folder / "people_list.csv"),
            str(folder / "summary.csv"),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.asyncio
async def test_a_broker_name_in_a_title_keeps_that_person_off_the_list() -> None:
    corpus = Corpus()
    page = corpus.data["search"]["title:servicenow:1"]["pages"][0]
    import yaml

    routing = ROOT / ".claude/skills/account-intelligence-platform/assets/routing.yaml"
    broker = yaml.safe_load(routing.read_text())["providers"]["p_people"]["name"]  # internal
    page["records"][0] = {
        **page["records"][0],
        "title": f"Chief Information Officer, {broker} programme",
    }
    result = await Kit(corpus).run()
    assert result.counts is not None
    # the person is kept off the file and counted; the export is never blocked by one row
    # (staging 2026-10-25: two leak hits held back all five files of a finished research)
    export = build_export(result.people, result.counts, result.count_sentences, result.source_codes)
    assert all(broker.casefold() not in r.title.casefold() for r in export.rows)
    assert any("distribution rules" in reason for _status, reason, _n in export.summary)


@pytest.mark.asyncio
async def test_only_title_exclusions_are_named_with_their_title() -> None:
    """People excluded by a title rule are named with the title as reported (the People List
    Summary sheet lists them); a tenant's own exclusion list is counted, never named."""
    result = await _result()
    assert result.counts is not None
    export = build_export(result.people, result.counts, result.count_sentences, result.source_codes)
    assert ("Ann Lee", "security", "Head of Information Security") in export.excluded
    ann = next(p for p in result.people if p.name == "Ann Lee")
    tenant = ann.model_copy(
        update={
            "classification": Classification(
                status="excluded", reason="tenant_exclusion", rule_id="tenant.exclusion"
            )
        }
    )
    people = [tenant if p is ann else p for p in result.people]
    export = build_export(people, result.counts, result.count_sentences, result.source_codes)
    assert "Ann Lee" not in {n for n, _r, _t in export.excluded}


@pytest.mark.asyncio
async def test_location_comes_from_the_profile_else_the_search_record() -> None:
    result = await _result()
    assert result.counts is not None
    listed = [p for p in result.people if p.status == "in_scope" and not p.internal_only]
    first, second = listed[0], listed[1]
    people = [
        p.model_copy(update={"location": "Search City"}) if p is second else p
        for p in result.people
    ]
    profile = Profile(
        person_key=first.person_key,
        profile_url=str(first.profile_url),
        location="Profile City",
        provider_code="p_test",
        raw_ref="raw/test",
        call_ref="call-test",
    )
    export = build_export(
        people, result.counts, result.count_sentences, result.source_codes, profiles=(profile,)
    )
    by_url = {r.profile_url: r for r in export.rows}
    assert by_url[str(first.profile_url)].location == "Profile City"
    assert by_url[str(second.profile_url)].location == "Search City"
