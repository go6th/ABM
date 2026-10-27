"""The workspace zip's run records (master prompt v2 sections 2 and 9.6): the entity lock, the
web searches, the pull log, the sense check, the pass 1 note and the URL map. Built end to end
from the synthetic corpus plus a stored lock and ledger lines, so the member list, the table, the
dedupe note, the wording rules and the byte determinism are all checked on a real build."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from account_intelligence.connectors.base import Operation
from account_intelligence.tracker.config import load_tracker_config
from exports.builder import ExportBuilder
from exports.config import ExportConfig, provider_hosts
from exports.content import PullCall, SenseCheckRow, SnapshotContent, WebHit
from exports.workspace import check_zip
from tests.exports.corpus import account
from tests.fakes.exports import MemoryStore

NEW_MEMBERS = (
    "data/entity_lock.json",
    "data/web_searches.json",
    "data/techstack_detections.json",
    "build/urls.json",
    "notes/pull_log.md",
    "notes/pass1.md",
    "notes/sense_check.md",
)
LOCK = {
    "legal_name": "Synthetic Alpha Ltd",
    "brand": "synthetic",
    "primary_domain": "alpha.example",
    "allowed_domains": ["alpha.example"],
    "careers_hosts": ["jobs.alpha.example"],
    "subsidiaries": [],
    "blocked_lookalikes": [{"host": "alfa.example", "title": "Alfa", "why": "close name"}],
    "home_url": "https://alpha.example",
    "redirected_to": "",
    "home_title": "Synthetic Alpha",
    "confirmed": True,
    "problems": [],
}


def _at(minute: int) -> datetime:
    return datetime(2026, 9, 30, 10, minute, tzinfo=UTC)


def _calls() -> tuple[PullCall, ...]:
    tool_host = sorted(provider_hosts())[0]
    return (
        PullCall(
            at=_at(1),
            operation="web_search",
            queries=("synthetic alpha careers", "Synthetic Alpha news"),
            result_count=3,
            cost_usd=0.014,
            hits=(
                WebHit(host="jobs.alpha.example"),
                WebHit(host="press.example", names_account=True),
                WebHit(host=tool_host),
            ),
        ),
        PullCall(  # the same query again: listed once with its count
            at=_at(2),
            operation="web_search",
            queries=("Synthetic Alpha News", "synthetic alpha careers"),
            result_count=3,
            cost_usd=0.014,
            hits=(WebHit(host="jobs.alpha.example"),),
        ),
        PullCall(
            at=_at(3),
            operation="web_search",
            queries=("alpha nothing here",),
            result_count=0,
            cost_usd=0.01,
        ),
        PullCall(  # every host is a look-alike or a page that does not name the account
            at=_at(4),
            operation="web_search",
            queries=("alfa widgets",),
            result_count=2,
            cost_usd=0.01,
            hits=(WebHit(host="alfa.example"), WebHit(host="blog.example", names_account=False)),
        ),
        PullCall(
            at=_at(5),
            operation="job_openings",
            step="job_board_by_lane",
            domain="alpha.example",
            since="2025-09-30",
            result_count=12,
            cost_usd=0.05,
        ),
        PullCall(at=_at(6), operation="tech_detections", domain="alpha.example", cost_usd=0.0),
    )


def _content(**update: object) -> SnapshotContent:
    base = account()
    return base.model_copy(
        update={
            "entity_lock": LOCK,
            "pull_calls": _calls(),
            "pull_failed": 2,
            "sense_check": SenseCheckRow(findings=3, proposals=1),
            **update,
        }
    )


async def _zip(
    make_builder: Callable[..., ExportBuilder], content: SnapshotContent
) -> tuple[bytes, dict[str, bytes]]:
    store = MemoryStore()
    result = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    zipped = next(d for d in result.deliverables if d.kind == "workspace")
    body = store.objects[zipped.object_key]
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return body, {n: z.read(n) for n in z.namelist()}


@pytest.mark.asyncio
async def test_members_in_order_and_listed_in_the_manifest(
    make_builder: Callable[..., ExportBuilder], cfg: ExportConfig
) -> None:
    body, members = await _zip(make_builder, _content())
    names = list(members)
    assert tuple(cfg.rules.workspace.required_members) == NEW_MEMBERS
    assert [n for n in names if n in NEW_MEMBERS] == list(NEW_MEMBERS)
    # the reference workspace's folders, in its order, after RESTORE.md and the manifest
    assert names[:2] == ["RESTORE.md", "MANIFEST.json"]
    folders = [n.split("/", 1)[0] for n in names[2:]]
    assert sorted(set(folders), key=folders.index) == ["deliverables", "data", "build", "notes"]
    assert folders == sorted(folders, key=["deliverables", "data", "build", "notes"].index)
    assert {f"build/{k}.json" for k in ("summary", "evidence", "people", "jobs", "tech")} <= set(
        names
    )
    listed = {m["name"] for m in json.loads(members["MANIFEST.json"])["members"]}
    assert {"RESTORE.md", *NEW_MEMBERS} <= listed
    assert check_zip(body, cfg.rules) == ()


@pytest.mark.asyncio
async def test_restore_note_is_written_from_the_run(
    make_builder: Callable[..., ExportBuilder], cfg: ExportConfig
) -> None:
    content = _content()
    builder = make_builder(content)
    _, members = await _zip(make_builder, content)
    restore = members["RESTORE.md"].decode()
    rs = cfg.rules.workspace.restore
    assert restore.startswith(f"# {content.account_name} workspace\n")
    for heading in (rs.layout_heading, rs.restore_heading, rs.queries_heading, rs.entity_heading):
        assert f"\n{heading}\n" in restore
    # the layout names every member of this zip, folder by folder
    for name in members:
        if "/" in name:
            folder, base = name.split("/", 1)
            line = next(x for x in restore.splitlines() if x.startswith(f"- {folder}/: "))
            assert base in line, name
    # the canonical definitions quote the run's own counts
    tech = builder.assemble(content).mapped["tech"].model
    assert tech is not None
    assert f"base {tech.base} postings naming any platform" in restore
    for row in tech.ranking:
        if row.lane != cfg.rules.labels.lane_outside:
            assert f"Answer on {content.as_of.isoformat()}: {row.count} ({row.open_now} open)" in (
                restore
            )
    jobs = builder.assemble(content).mapped["jobs"].model
    rows_line = next(line for line in jobs.counts if line.key == "file.rows")
    assert f'"{rows_line.label}"' in restore and f": {rows_line.value}." in restore
    assert rs.query_zero in restore
    # the entity rules come from the stored lock
    assert "alpha.example" in restore and "careers boards jobs.alpha.example" in restore
    assert rs.entity_lookalikes.format(n=1) in restore
    assert "alfa" not in restore.casefold()  # a look-alike is counted, never named here


@pytest.mark.asyncio
async def test_restore_without_a_lock_says_so(
    make_builder: Callable[..., ExportBuilder], cfg: ExportConfig
) -> None:
    _, members = await _zip(make_builder, _content(entity_lock=None))
    assert cfg.rules.workspace.restore.entity_none in members["RESTORE.md"].decode()


@pytest.mark.asyncio
async def test_detections_member_holds_the_stored_rows(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    content = _content()
    _, members = await _zip(make_builder, content)
    data = json.loads(members["data/techstack_detections.json"])
    assert data["source"] == "6th agents" and data["count"] == len(content.detections)
    assert [d["technology"] for d in data["detections"]] == [
        d.technology for d in content.detections
    ]
    keys = {"technology", "first_seen", "last_seen", "source_url"}
    assert all(set(d) == keys for d in data["detections"])


@pytest.mark.asyncio
async def test_pull_log_table_dedupe_and_completeness(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    content = _content()
    _, members = await _zip(make_builder, content)
    log = members["notes/pull_log.md"].decode()
    assert "| Step | Tool role | Parameters as sent | Result count | Cost | Output file |" in log
    rows = [line for line in log.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert len(rows) == 5  # six calls; the first two ask the same two queries in another order
    assert '"synthetic alpha careers", "Synthetic Alpha news" (repeated 2 times)' in rows[0]
    assert "| web_search | open web search |" in rows[0]
    assert (
        "job_board_by_lane | job and news feed | domain alpha.example; since 2025-09-30 | 12 |"
        in log
    )
    assert "| 0.05 |" in log and "| 0.01 |" in log
    assert "| technology feed |" in log and "| not counted |" in log
    assert "Calls recorded: 6" in log
    assert (
        "Unique search queries: 4 (distinct query text, case ignored, among 6 queries sent)" in log
    )
    assert "Duplicate search queries: 2" in log
    assert "Search queries with no results: 1" in log
    assert "Calls that failed and returned nothing: 2" in log
    assert "Entity lock: recorded" in log


@pytest.mark.asyncio
async def test_an_identical_call_is_listed_once_with_its_count(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    same = (
        PullCall(
            at=_at(1), operation="web_search", queries=("Alpha Ltd",), result_count=4, cost_usd=0.01
        ),
        PullCall(
            at=_at(2), operation="web_search", queries=("alpha ltd",), result_count=4, cost_usd=0.01
        ),
        PullCall(
            at=_at(3), operation="web_search", queries=("alpha ltd",), result_count=4, cost_usd=0.0
        ),
    )
    _, members = await _zip(make_builder, _content(pull_calls=same))
    log = members["notes/pull_log.md"].decode()
    assert log.count('"Alpha Ltd"') == 1 and "(repeated 3 times)" in log
    assert "| 0.02 |" in log
    searches = json.loads(members["data/web_searches.json"])
    assert [s["q"] for s in searches] == ["Alpha Ltd"]


@pytest.mark.asyncio
async def test_web_searches_follow_the_entity_lock(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    _, members = await _zip(make_builder, _content())
    searches = json.loads(members["data/web_searches.json"])
    by_q = {s["q"]: s for s in searches}
    assert list(searches[0]) == [
        "q",
        "date",
        "tool_role",
        "count",
        "domains",
        "kept",
        "dropped_reason",
    ]
    assert len(searches) == 4  # unique (lower query, role)
    careers = by_q["synthetic alpha careers"]
    assert careers["kept"] is True and careers["dropped_reason"] is None
    assert careers["tool_role"] == "open web search" and careers["date"] == "2026-09-30"
    assert careers["domains"] == [
        "alpha.example",
        "press.example",
    ]  # a data tool's own site is omitted
    assert by_q["alpha nothing here"]["kept"] is False
    assert by_q["alpha nothing here"]["dropped_reason"] == "No results returned."
    assert by_q["alfa widgets"]["kept"] is False
    assert "belongs to the account" in by_q["alfa widgets"]["dropped_reason"]


@pytest.mark.asyncio
async def test_no_provider_names_credits_or_call_references(
    make_builder: Callable[..., ExportBuilder], cfg: ExportConfig
) -> None:
    _, members = await _zip(make_builder, _content())
    text = "\n".join(members[n].decode() for n in NEW_MEMBERS if n != "build/urls.json").casefold()
    assert "credits" not in text and "credit" not in text
    for host in provider_hosts():
        assert host not in text
    for op in Operation:
        assert f"p_{op.value}" not in text
    assert "p_search" not in text and "p_signals" not in text and "p_org" not in text


@pytest.mark.asyncio
async def test_a_query_naming_a_data_tool_is_masked_not_fatal(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    """An account whose own name is a tool's ("Parallel") must still get its zip: the name shows
    the scope mask in the pull log and web searches, and the scan finds nothing."""
    bad = (
        PullCall(at=_at(1), operation="web_search", queries=("pricing parallel search review",)),
    )
    content = _content(pull_calls=bad)
    result = await make_builder(content, MemoryStore()).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    assert "workspace" in {d.kind for d in result.deliverables}


@pytest.mark.asyncio
async def test_two_builds_are_byte_identical(make_builder: Callable[..., ExportBuilder]) -> None:
    first, _ = await _zip(make_builder, _content())
    second, _ = await _zip(make_builder, _content())
    assert first == second


@pytest.mark.asyncio
async def test_nothing_recorded_says_so_and_invents_no_rows(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    content = account().model_copy(update={"sources": account().sources})
    _, members = await _zip(make_builder, content)
    assert json.loads(members["data/entity_lock.json"]) == {
        "note": "No entity lock recorded for this run.",
        "recorded": False,
    }
    assert json.loads(members["data/web_searches.json"]) == []
    assert "No calls recorded for this run." in members["notes/pull_log.md"].decode()
    assert "Entity lock: none recorded" in members["notes/pull_log.md"].decode()
    assert members["notes/sense_check.md"] == b"No sense check recorded for this run.\n"


@pytest.mark.asyncio
async def test_sense_check_lists_conflicting_facts_in_plain_words(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    base = account()
    facts = list(base.facts)
    facts[1] = facts[1].model_copy(
        update={"flags": ("conflicting",), "confirming_question": "Can the account confirm it?"}
    )
    _, members = await _zip(make_builder, _content(facts=tuple(facts)))
    note = members["notes/sense_check.md"].decode()
    assert "Findings raised: 3. Corrections proposed: 1." in note
    assert "Findings the sense check marked Conflicting" in note
    assert f"{facts[1].value_text} (graded Probable; the sources disagree)" in note
    assert facts[1].id not in note and facts[1].attribute_key not in note


@pytest.mark.asyncio
async def test_pass1_note_follows_the_tracker_reason(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    base = account()
    _, read = await _zip(make_builder, base)
    assert read["notes/pass1.md"] == (
        b"Internal history was read; its findings stay with the account owner and are not part"
        b" of this package.\n"
    )
    reason = load_tracker_config().reason_codes["no_internal_access"]
    lines = tuple(
        t.model_copy(update={"status": "Not done", "reason": reason})
        if t.key in ("prior_customer_status", "connections_alumni")
        else t
        for t in base.tracker
    )
    _, skipped = await _zip(make_builder, base.model_copy(update={"tracker": lines}))
    assert skipped["notes/pass1.md"] == b"pass 1 skipped: no internal access\n"


@pytest.mark.asyncio
async def test_url_map_is_the_source_registry_in_code_order(
    make_builder: Callable[..., ExportBuilder],
) -> None:
    content = _content()
    _, members = await _zip(make_builder, content)
    urls = json.loads(members["build/urls.json"])
    assert urls == {s.code: s.url for s in content.sources}
    assert list(urls) == sorted(urls)


@pytest.mark.asyncio
async def test_a_zip_missing_a_run_record_fails_its_check(
    make_builder: Callable[..., ExportBuilder], cfg: ExportConfig
) -> None:
    body, members = await _zip(make_builder, _content())
    for gone in NEW_MEMBERS:
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(body)) as src, zipfile.ZipFile(out, "w") as dst:
            for info in src.infolist():
                if info.filename != gone:
                    dst.writestr(info, src.read(info.filename))
        assert f"missing member {gone}" in check_zip(out.getvalue(), cfg.rules)


def test_every_operation_has_a_neutral_role_and_an_output(cfg: ExportConfig) -> None:
    pl = cfg.rules.workspace.pull_log
    for op in Operation:
        assert op.value in pl.roles and op.value in pl.outputs
    assert not any(role.startswith("p_") for role in pl.roles.values())
