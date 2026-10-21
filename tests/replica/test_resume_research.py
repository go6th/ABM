"""Research that never loses data (Phase 4B section 4): a run with no progress is marked stalled
once and its owner told; Resume research continues a stopped or stalled run on the same account
(reusing what it holds), says how many steps are already done, uses none of the user's
researches, and refuses a run that is still moving or finished whole."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.replica.test_workspace_api import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _run(tenant: UUID, user: str, status: str, *, idle_minutes: int = 0) -> tuple[UUID, str]:
    """An account of the user's and one run of it, with two of three steps done."""
    domain = f"resume-{uuid4().hex[:8]}.example"
    (account,) = _db(
        "INSERT INTO accounts (tenant_id, owner_user_id, added_by, domain, name)"
        " VALUES (%s, %s, %s, %s, 'Resume Co') RETURNING id",
        tenant,
        user,
        user,
        domain,
    )[0]
    finished = "now()" if status in ("COMPLETED", "PARTIAL", "FAILED") else "NULL"
    (run,) = _db(
        # a scoped research: its run records the three rows it plans (research_scopes.yaml)
        "INSERT INTO account_runs (tenant_id, account_id, owner_user_id, status, graph_version,"
        " budget_json, reserved_credits, idempotency_key, budget_usd, finished_at,"
        f" finished_reason, progress_at, run_input) VALUES (%s, %s, %s, %s, 'test', '{{}}', 0,"
        f" %s, 5, {finished}, %s, now() - make_interval(mins => %s),"
        """ '{"scope_rows": ["company_news", "job_postings", "people"]}') RETURNING id""",
        tenant,
        account,
        user,
        status,
        uuid4().hex,
        "worker_lost" if finished != "NULL" else None,
        idle_minutes,
    )[0]
    tracked = {"RUNNING": "running", "FAILED": "failed"}.get(status, "completed")
    _db(
        "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
        " credit_cap, started_at, finished_at) VALUES (%s, %s, %s, 'full', %s, 'test', 1,"
        f" now() - interval '2 hours', {'NULL' if tracked == 'running' else 'now()'})",
        run,
        tenant,
        account,
        tracked,
    )
    # three planned rows (two done) and one the research did not plan (a scoped research marks
    # the rows outside its scope "not planned" at once): the step counts never include it
    for key, state, reason in (
        ("company_news", "done", None),
        ("job_postings", "done", None),
        ("people", None, None),
        ("technology_detections", "blocked", "not_planned"),
    ):
        _db(
            "INSERT INTO coverage_rows (tenant_id, run_id, row_key, priority, status, proof,"
            " reason_code) VALUES (%s, %s, %s, 'high', %s, %s, %s)",
            tenant,
            run,
            key,
            state,
            '{"calls": []}' if state else None,
            reason,
        )
    return run, domain


async def test_a_stalled_run_is_marked_once_and_its_owner_told(world: dict[str, Any]) -> None:
    from account_intelligence.db.session import SessionLocal, set_system, tenant_txn
    from account_intelligence.runs.sweeper import mark_stalled

    user = f"stall-{uuid4().hex[:6]}"
    stuck, _ = _run(world["a"], user, "RUNNING", idle_minutes=45)
    moving, _ = _run(world["a"], user, "RUNNING", idle_minutes=1)
    set_system()
    now = datetime.now(UTC)
    first = await mark_stalled(SessionLocal, tenant_txn, now)
    assert stuck in first and moving not in first
    assert await mark_stalled(SessionLocal, tenant_txn, now) == []  # once
    assert _db(
        "SELECT kind FROM notifications WHERE tenant_id = %s AND user_id = %s",
        world["a"],
        user,
    ) == [("research_stalled",)]


async def test_resume_continues_a_stopped_run_and_says_what_is_done(world: dict[str, Any]) -> None:
    user = f"resume-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    run, domain = _run(world["a"], user, "PARTIAL")
    before = (await _call(plane, "GET", "/v1/me/limits")).json()["researches"]["used"]
    key = str(uuid4())
    out = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(run), "request_id": key}
    )
    assert out.status_code == 201, out.text
    body = out.json()
    assert (body["resumes_run_id"], body["domain"]) == (str(run), domain)
    assert (body["steps_done"], body["steps_total"]) == (2, 3)
    assert _db("SELECT resume_from FROM research_jobs WHERE id = %s", body["job_id"]) == [(run,)]
    # a retried click is the same resume; neither uses one of the user's researches
    again = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(run), "request_id": key}
    )
    assert again.json()["job_id"] == body["job_id"]
    assert (await _call(plane, "GET", "/v1/me/limits")).json()["researches"]["used"] == before


async def test_a_stalled_run_is_ended_partial_and_resumed(world: dict[str, Any]) -> None:
    user = f"stalled-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    run, _ = _run(world["a"], user, "RUNNING", idle_minutes=60)
    busy = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(run), "request_id": str(uuid4())}
    )
    assert busy.status_code == 409 and busy.json()["type"].endswith("still_running")
    _db("UPDATE account_runs SET stalled_at = now() WHERE id = %s", run)
    out = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(run), "request_id": str(uuid4())}
    )
    assert out.status_code == 201, out.text
    (status,) = _db("SELECT status, finished_reason FROM account_runs WHERE id = %s", run)
    assert status[1] == "stalled" and status[0] in ("PARTIAL", "FAILED")


async def test_a_finished_run_or_another_users_run_cannot_be_resumed(world: dict[str, Any]) -> None:
    user = f"done-{uuid4().hex[:6]}"
    plane = _plane(world["a"], user, world["payloads"])
    done, _ = _run(world["a"], user, "COMPLETED")
    whole = await _call(
        plane, "POST", "/v1/research/resume", json={"run_id": str(done), "request_id": str(uuid4())}
    )
    assert whole.status_code == 409
    theirs, _ = _run(world["a"], f"other-{uuid4().hex[:6]}", "PARTIAL")
    hidden = await _call(
        plane,
        "POST",
        "/v1/research/resume",
        json={"run_id": str(theirs), "request_id": str(uuid4())},
    )
    assert hidden.status_code == 404  # private workspaces: another user's run is not there


async def test_a_job_left_running_after_its_run_ended_is_closed_and_not_shown(
    world: dict[str, Any],
) -> None:
    """Seen on staging 2026-10-15: the run finished but its job stayed 'running', so the account
    kept saying "Refreshing from the web" and hid Resume research. The workspace no longer shows
    such a job and the sweep settles it once (credits released)."""
    from account_intelligence.api.main import _close_ended_jobs
    from account_intelligence.db.session import set_system

    user = "priya-test"
    run, domain = _run(world["a"], user, "PARTIAL")
    (account,) = _db("SELECT account_id FROM account_runs WHERE id = %s", run)[0]
    (job,) = _db(
        "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
        " credits_estimated, requested_by, run_id, started_at) VALUES (%s, %s, 'new_research',"
        " 'full', 'running', to_char(now(), 'YYYY-MM'), 0, %s, %s, now()) RETURNING id",
        world["a"],
        account,
        user,
        run,
    )[0]
    plane = _plane(world["a"], user, world["payloads"])
    ws = await _call(plane, "GET", f"/v1/accounts/{domain}/workspace")
    assert ws.status_code == 200, ws.text
    assert ws.json()["open_job"] is None
    set_system()
    assert await _close_ended_jobs() >= 1
    assert _db("SELECT status FROM research_jobs WHERE id = %s", job) == [("done",)]
    assert await _close_ended_jobs() == 0 or _db(
        "SELECT status FROM research_jobs WHERE id = %s", job
    ) == [("done",)]


async def test_every_stop_reason_is_said_in_words() -> None:
    """A stopped research says why (staging 2026-10-15 showed only "It stopped before
    finishing." for a reason the screen did not know)."""
    from account_intelligence.replica.workspace import stop_text

    assert "quality check" in stop_text("gate_failed")
    assert stop_text("build_failed:missing_narrative") == (
        "The files could not be built from what was found"
        " (no opening narrative could be written from the findings)."
    )
    # the builder's rules, in words, each; an unknown rule shows as its code
    assert stop_text("build_failed:banned_dash,new_rule") == (
        "The files could not be built from what was found"
        " (a dash used as punctuation in a client file; new rule)."
    )
    assert stop_text("build_failed") == (
        "The files could not be built from what was found (no detail)."
    )
    assert stop_text("something_new") == "It stopped before finishing (reason: something new)."
    assert "usage limit" in stop_text("model_credits_exhausted")


async def test_the_quality_checks_reasons_are_said_in_words() -> None:
    from account_intelligence.replica.workspace import GATE_CHECK_TEXT, gate_text, stop_text

    defects = [
        {"check": "fact_citations", "item": "f1:no_citation"},
        {"check": "fact_citations", "item": "f2:no_citation"},
        {"check": "client_dashes", "item": "summary:1"},
    ]
    said = gate_text(defects)
    assert said.startswith("The quality check held the files back: 2 x a finding without a source")
    assert "a dash used as punctuation in a client file" in said and said.endswith("each step.")
    assert stop_text("gate_failed", defects) == said
    assert "quality check" in stop_text("gate_failed", [])  # no stored verdict: the generic text
    assert gate_text([{"check": "new_check", "item": "x"}]).startswith(
        "The quality check held the files back: new check"
    )
    assert set(GATE_CHECK_TEXT) >= {"tracker_rows", "client_terms", "leak_check", "deliverable_set"}


async def test_the_account_card_names_the_quality_checks_that_held_the_files(
    world: dict[str, Any],
) -> None:
    from account_intelligence.db.session import set_system, tenant_txn
    from account_intelligence.replica.workspace import stopped_run

    user = f"gate-{uuid4().hex[:6]}"
    run, _ = _run(world["a"], user, "PARTIAL")
    _db("UPDATE account_runs SET finished_reason = 'gate_failed' WHERE id = %s", run)
    (account,) = _db("SELECT account_id FROM account_runs WHERE id = %s", run)[0]
    _db(
        "INSERT INTO run_gate_results (tenant_id, account_id, run_id, verdict, defects)"
        " VALUES (%s, %s, %s, 'fail', %s)",
        world["a"],
        account,
        run,
        '[{"check": "third_party_domains", "item": "evidence:1"},'
        ' {"check": "tracker_rows", "item": "people:no_reason"}]',
    )
    set_system()
    async with tenant_txn(world["a"]) as s:
        card = await stopped_run(s, account)
    assert card is not None and not card["stalled"]
    assert "a data provider named in a client file" in card["reason"]
    assert "a step without a status or a reason" in card["reason"]


async def test_no_new_findings_is_said_as_nothing_found() -> None:
    from account_intelligence.replica.workspace import stop_text

    assert stop_text("no_new_findings") == "Nothing new was found on the web for this research."
