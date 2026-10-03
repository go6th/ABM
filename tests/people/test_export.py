"""Output safety (spec 9.9, P16): only in-scope people by name, a Summary of counts by status and
reason, no provider code, call reference, credit figure or contact detail, and the client-output
scanner passes on the written files."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import pytest

from account_intelligence.safety.client_output import ClientOutputLeak
from people.export import build_export
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
async def test_a_broker_name_in_a_title_blocks_the_export() -> None:
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
    with pytest.raises(ClientOutputLeak):
        build_export(result.people, result.counts, result.count_sentences, result.source_codes)
