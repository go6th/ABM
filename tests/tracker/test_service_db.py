"""Tracker service, guard and database defences on real Postgres (spec 9.1, 9.5 to 9.8)."""

from __future__ import annotations

import asyncio
import json

import psycopg
import pytest
from psycopg import errors

from account_intelligence.db.session import tenant_txn
from account_intelligence.safety.client_output import scan_text
from account_intelligence.tracker.config import load_checklist
from account_intelligence.tracker.errors import ProofMismatch, VersionConflict
from account_intelligence.tracker.guard import finish_run
from account_intelligence.tracker.model import Actor, ReasonCode, Status
from tests.tracker.world import (
    World,
    admin_dsn,
    fetch,
    ledger_done,
    ledger_failed,
    make_world,
    tracker,
    txn,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _seeded() -> World:
    w = make_world()
    async with txn(w) as t:
        assert await t.seed(w.run, w.tenant) is True
    return w


async def _complete_every_row(w: World) -> None:
    """Report every row from its owning stage with one successful call (synthetic)."""
    from account_intelligence.workflow.spec import load_spec

    owners = load_spec().coverage_owners
    async with txn(w) as t:
        for row in load_checklist().rows:
            ref = ledger_done(w, record_count=2, queries=(f"synthetic {row.key}",))
            await t.record_outcome(
                w.tenant, w.run, row.key, [ref], Actor.STAGE, stage=owners[row.key]
            )


async def test_seed_is_the_checklist_pinned_and_idempotent() -> None:
    w = await _seeded()
    rows = fetch(
        "SELECT row_key, priority, owner_stage, status FROM coverage_rows WHERE run_id = %s",
        w.run,
    )
    cfg = load_checklist()
    assert {r[0] for r in rows} == {r.key for r in cfg.rows} and len(rows) == 33
    assert all(r[2] is not None and r[3] is None for r in rows)  # owned, unset
    ((version, digest, count, policy),) = fetch(
        "SELECT checklist_version, checklist_hash, seeded_row_count, pinned_policy"
        " FROM run_coverage_meta WHERE run_id = %s",
        w.run,
    )
    assert (version, digest, count) == (cfg.version, cfg.sha256, 33)
    assert policy == {
        "high_rows_allowed_for_completed": ["done", "none"],
        "high_reasons_allowed_for_completed": ["not_in_uploaded_data", "not_searched_file_only"],
    }
    async with txn(w) as t:
        assert await t.seed(w.run, w.tenant) is False  # re-seed is a no-op
    assert len(fetch("SELECT 1 FROM coverage_rows WHERE run_id = %s", w.run)) == 33


async def test_events_record_the_real_from_status_and_a_gapless_seq() -> None:
    w = await _seeded()
    first = ledger_done(w, record_count=0, queries=("one",))
    second = ledger_done(w, record_count=3, queries=("two",))
    async with txn(w) as t:
        await t.record_outcome(w.tenant, w.run, "company_news", [first], Actor.STAGE, stage="news")
    async with txn(w) as t:
        await t.record_outcome(
            w.tenant, w.run, "company_news", [first, second], Actor.STAGE, stage="news"
        )
    async with txn(w) as t:
        await t.downgrade(
            w.tenant, w.run, "company_news", Status.PARTIAL, Actor.JUDGE, ReasonCode.CLAIMS_DROPPED
        )
    events = fetch(
        "SELECT seq, from_status, to_status, actor, stage FROM coverage_row_events"
        " WHERE run_id = %s AND row_key = 'company_news' ORDER BY seq",
        w.run,
    )
    assert events == [
        (1, None, "none", "stage", "news"),
        (2, "none", "done", "stage", "news"),
        (3, "done", "partial", "judge", None),
    ]
    ((proof, attempts),) = fetch(
        "SELECT proof, attempts FROM coverage_rows WHERE run_id = %s AND row_key = 'company_news'",
        w.run,
    )
    assert len(proof["call_refs"]) == 2 and proof["notes"]  # downgrade kept the proof, added a note
    assert attempts == 2  # downgrades do not bump attempts


async def test_replay_after_a_crash_is_unchanged_and_writes_no_event() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    async with txn(w) as t:
        first = await t.record_outcome(
            w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news"
        )
        digest = await t.digest(w.run)
    async with txn(w) as t:  # the stage replays after a kill between commit and checkpoint
        again = await t.record_outcome(
            w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news"
        )
        assert await t.digest(w.run) == digest
    assert (first.unchanged, again.unchanged) == (False, True)
    assert len(fetch("SELECT 1 FROM coverage_row_events WHERE run_id = %s", w.run)) == 1


async def test_rolled_back_stage_leaves_no_row_change() -> None:
    """Spec 8.1: a Done row cannot outlive the stage's rolled-back fact writes."""
    w = await _seeded()
    ref = ledger_done(w)
    with pytest.raises(RuntimeError):
        async with tenant_txn(w.tenant) as session:
            await tracker(session).record_outcome(
                w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news"
            )
            raise RuntimeError("fact write failed")
    assert fetch(
        "SELECT status FROM coverage_rows WHERE run_id = %s AND row_key = 'company_news'", w.run
    ) == [(None,)]


async def test_guard_never_completes_with_gaps_and_the_db_refuses_it_directly() -> None:
    w = await _seeded()
    async with tenant_txn(w.tenant) as session:
        result = await finish_run(session, tracker(session), w.run, "RUNNING", "COMPLETED")
    assert result.status == "PARTIAL" and not result.verdict.can_complete
    ((status, reason),) = fetch(
        "SELECT status, finished_reason FROM account_runs WHERE id = %s", w.run
    )
    assert status == "PARTIAL" and len(json.loads(reason)["blocked_by"]) == 33
    assert fetch("SELECT status, error FROM runs WHERE id = %s", w.run)[0][0] == "failed"


async def test_guard_completes_a_fully_reported_run_once() -> None:
    w = await _seeded()
    await _complete_every_row(w)
    async with tenant_txn(w.tenant) as session:
        result = await finish_run(session, tracker(session), w.run, "RUNNING", "COMPLETED")
    assert result.status == "COMPLETED" and result.verdict.level == "complete"
    assert fetch("SELECT status FROM runs WHERE id = %s", w.run) == [("completed",)]
    with pytest.raises(VersionConflict):  # a second finisher loses the race and changes nothing
        async with tenant_txn(w.tenant) as session:
            await finish_run(session, tracker(session), w.run, "RUNNING", "COMPLETED")


def _as_app_rw(tenant: object, sql: str, *params: object) -> None:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(tenant),),
        )
        c.execute(sql, params)


async def test_db_refuses_completed_as_app_rw_while_a_row_is_unset_or_swept() -> None:
    w = await _seeded()
    with pytest.raises(errors.CheckViolation):
        _as_app_rw(
            w.tenant,
            "UPDATE account_runs SET status='COMPLETED', finished_at=now() WHERE id = %s",
            w.run,
        )
    async with txn(w) as t:
        await t.sweep(w.run, w.tenant, ReasonCode.SWEPT_FAILED)
    with pytest.raises(errors.CheckViolation):
        _as_app_rw(
            w.tenant,
            "UPDATE account_runs SET status='COMPLETED', finished_at=now() WHERE id = %s",
            w.run,
        )


async def test_db_refuses_event_update_event_delete_and_row_delete_as_app_rw() -> None:
    w = await _seeded()
    ref = ledger_done(w)
    async with txn(w) as t:
        await t.record_outcome(w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news")
    for sql in (
        "UPDATE coverage_row_events SET to_status = 'done' WHERE run_id = %s",
        "DELETE FROM coverage_row_events WHERE run_id = %s",
        "DELETE FROM coverage_rows WHERE run_id = %s",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(w.tenant, sql, w.run)


async def test_db_refuses_a_status_without_proof_and_a_blocked_without_reason() -> None:
    w = await _seeded()
    for sql in (
        "UPDATE coverage_rows SET status = 'done' WHERE run_id = %s",
        "UPDATE coverage_rows SET status = 'blocked', proof = '{}' WHERE run_id = %s",
        "UPDATE coverage_rows SET status = 'pending', proof = '{}' WHERE run_id = %s",
    ):
        with pytest.raises(errors.CheckViolation):
            _as_app_rw(w.tenant, sql, w.run)


async def test_racing_writers_on_one_row_produce_one_event_per_accepted_version() -> None:
    """Spec 9.6: ten writers race on one row; nothing is lost or duplicated."""
    w = await _seeded()
    refs = [ledger_done(w, queries=(f"q{i}",)) for i in range(10)]

    async def write(i: int) -> None:
        async with txn(w) as t:
            await t.record_outcome(
                w.tenant, w.run, "company_news", refs[: i + 1], Actor.STAGE, stage="news"
            )

    results = await asyncio.gather(*(write(i) for i in range(10)), return_exceptions=True)
    events = fetch(
        "SELECT seq FROM coverage_row_events WHERE run_id = %s AND row_key = 'company_news'"
        " ORDER BY seq",
        w.run,
    )
    seqs = [e[0] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))  # gapless, no duplicate
    ((version,),) = fetch(
        "SELECT version FROM coverage_rows WHERE run_id = %s AND row_key = 'company_news'", w.run
    )
    assert version == len(seqs)  # one event per accepted version
    # a refused writer is a typed tracker error (stale proof is not cumulative), never a crash
    from account_intelligence.tracker.errors import TrackerError

    assert all(r is None or isinstance(r, TrackerError) for r in results)


async def test_twenty_writers_on_distinct_rows_lose_nothing() -> None:
    w = await _seeded()
    from account_intelligence.workflow.spec import load_spec

    owners = load_spec().coverage_owners
    keys = [r.key for r in load_checklist().rows][:20]
    refs = {k: ledger_done(w) for k in keys}

    async def write(key: str) -> None:
        async with txn(w) as t:
            await t.record_outcome(
                w.tenant, w.run, key, [refs[key]], Actor.STAGE, stage=owners[key]
            )

    await asyncio.gather(*(write(k) for k in keys))
    done = fetch("SELECT count(*) FROM coverage_rows WHERE run_id = %s AND status = 'done'", w.run)
    assert done == [(20,)]


async def test_two_tenants_with_the_same_domain_never_cross() -> None:
    a, b = await _seeded(), await _seeded()
    ref_b = ledger_done(b)
    with pytest.raises(ProofMismatch):  # b's call is not a call of a's run
        async with txn(a) as t:
            await t.record_outcome(
                a.tenant, a.run, "company_news", [ref_b], Actor.STAGE, stage="news"
            )
    async with tenant_txn(a.tenant) as session:
        rows = await tracker(session).repo.get_all_rows(b.run)
    assert rows == []  # RLS: tenant a cannot even see b's rows


async def test_client_view_is_allow_listed_scanned_and_ordered() -> None:
    w = await _seeded()
    ok = ledger_done(w, record_count=5, queries=("synthetic news",))
    bad = ledger_failed(w, error="RateLimited")
    async with txn(w) as t:
        await t.record_outcome(w.tenant, w.run, "company_news", [ok], Actor.STAGE, stage="news")
        await t.record_outcome(
            w.tenant, w.run, "job_board_by_lane", [bad], Actor.STAGE, stage="jobs"
        )
        view = await t.view(w.run, "client")
        operator = await t.view(w.run, "operator")
    text = json.dumps(view)
    assert not scan_text(text) and "p_search" not in text and ok not in text and bad not in text
    assert set(view["rows"][0]) == {"row", "status", "priority", "reason", "tools", "results"}
    statuses = [r["status"] for r in view["rows"]]
    assert statuses.index("Blocked") < statuses.index("Done")  # open work first
    blocked = next(r for r in view["rows"] if r["status"] == "Blocked")
    assert blocked["reason"] == "Source rate limited" and blocked["tools"] == ["web_search"]
    assert any("of 19" in s for s in view["tally"])  # counts name their base
    assert ok in json.dumps(operator)  # the operator view carries the references


async def test_verdict_uses_the_pinned_policy_not_todays_config() -> None:
    w = await _seeded()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "UPDATE run_coverage_meta SET pinned_policy = %s WHERE run_id = %s",
            (json.dumps({"high_rows_allowed_for_completed": ["done", "none", "partial"]}), w.run),
        )
    await _complete_every_row(w)
    async with txn(w) as t:
        await t.downgrade(
            w.tenant, w.run, "company_news", Status.PARTIAL, Actor.GATE, ReasonCode.CLAIMS_DROPPED
        )
        verdict = await t.verdict(w.run)
    assert verdict.can_complete and verdict.level == "complete_with_gaps"


def _kill(mode: str, w: World, ref: str) -> int:
    import os
    import subprocess
    import sys
    from pathlib import Path

    args = [mode, str(w.tenant), str(w.account), str(w.run), w.domain, ref]
    return subprocess.run(
        [sys.executable, "-m", "tests.tracker.kill_worker", *args],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(),
        capture_output=True,
        timeout=120,
    ).returncode


async def test_sigkill_mid_transaction_is_atomic_and_after_commit_replays_unchanged() -> None:
    """Spec 9.6 and 9.7: row and event are both present or both absent; a replay after a kill
    between the commit and the checkpoint writes no second event."""
    w = await _seeded()
    ref = ledger_done(w)
    count = "SELECT count(*) FROM coverage_row_events WHERE run_id = %s"
    status = "SELECT status FROM coverage_rows WHERE run_id = %s AND row_key = 'company_news'"
    assert _kill("mid_txn", w, ref) == 137
    assert (fetch(status, w.run), fetch(count, w.run)) == ([(None,)], [(0,)])
    assert _kill("after_commit", w, ref) == 137
    assert (fetch(status, w.run), fetch(count, w.run)) == ([("done",)], [(1,)])
    async with txn(w) as t:
        again = await t.record_outcome(
            w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news"
        )
    assert again.unchanged and fetch(count, w.run) == [(1,)]


async def test_dropped_claims_downgrade_from_surviving_evidence_never_to_none() -> None:
    """T14: the judge drops claims; the row is re-derived from the calls that still support it."""
    w = await _seeded()
    a = ledger_done(w, record_count=2, queries=("a",))
    b = ledger_done(w, record_count=2, queries=("b",))
    async with txn(w) as t:
        await t.record_outcome(w.tenant, w.run, "company_news", [a, b], Actor.STAGE, stage="news")
        # every claim survives: no downgrade
        assert (
            await t.downgrade_for_dropped_claims(
                w.tenant, w.run, "company_news", [a, b], Actor.JUDGE
            )
            is None
        )
        # nothing survives: Not done (never None), proof kept
        result = await t.downgrade_for_dropped_claims(
            w.tenant, w.run, "company_news", [], Actor.JUDGE
        )
        assert result is not None and result.status is Status.NOT_DONE
        assert result.reason_code is ReasonCode.CLAIMS_DROPPED
        # a row that is not Done or Partial is left alone
        assert (
            await t.downgrade_for_dropped_claims(w.tenant, w.run, "company_news", [], Actor.JUDGE)
            is None
        )


async def test_unseeded_run_verdict_and_bad_audience() -> None:
    w = make_world()
    async with txn(w) as t:
        verdict = await t.verdict(w.run)
        assert not verdict.can_complete and verdict.blockers[0]["reason"] == "run_not_seeded"
        with pytest.raises(ValueError):
            await t.view(w.run, "prospect")
        with pytest.raises(Exception, match="swept"):  # sweep needs a swept_* reason
            await t.sweep(w.run, w.tenant, ReasonCode.DEADLINE)
        with pytest.raises(ValueError):
            from account_intelligence.tracker.guard import finish_run as finish

            await finish(t.repo.session, t, w.run, "RUNNING", "RUNNING")


async def test_lane_switched_off_in_run_input_is_none_out_of_scope() -> None:
    """T4(b): only a verified switch-off in the run input yields None without a search."""
    from account_intelligence.tracker.adapters import RunInputAdapter
    from account_intelligence.tracker.config import ChecklistConfig, ChecklistRow

    w = await _seeded()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "UPDATE account_runs SET run_input = %s WHERE id = %s",
            (json.dumps({"lanes_off": ["veeva"]}), w.run),
        )
    base = load_checklist()
    rows = tuple(
        r.model_copy(update={"lane": "veeva"}) if r.key == "company_news" else r for r in base.rows
    )

    class LaneChecklist:
        def get_checklist(self) -> ChecklistConfig:
            return base.model_copy(update={"rows": rows})

    async with tenant_txn(w.tenant) as session:
        t = tracker(session)
        t.checklist = LaneChecklist()
        assert await RunInputAdapter(session).is_lane_in_scope(w.tenant, w.run, "veeva") is False
        result = await t.record_outcome(
            w.tenant,
            w.run,
            "company_news",
            [],
            Actor.STAGE,
            ReasonCode.LANE_NOT_IN_SCOPE,
            stage="news",
        )
    assert (result.status, result.reason_code) == (Status.NONE, ReasonCode.OUT_OF_SCOPE)
    assert ChecklistRow  # imported for the typed fixture above
