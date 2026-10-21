"""The Research screen, live (owner request 2026-10-21): every web research running and recent on
one page, Stop on each, and data on screen within a second of being stored. Read from the
database each time (SYNTHETIC tenant and company).

GUARANTEE(test_live_snapshot_shows_a_fact_as_soon_as_it_is_stored)
GUARANTEE(test_live_stream_sends_a_new_snapshot_when_a_fact_is_stored)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN
from tests.replica.conftest import _call, _plane

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _db(sql: str, *params: object) -> list[Any]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def _running(plane: Any, tenant: UUID, domain: str) -> tuple[str, UUID]:
    """A confirmed new research whose run has started (the worker's claim, no graph run)."""
    from account_intelligence.replica import jobs

    body = {"request_id": str(uuid4()), "company_name": "Liveco", "domain": domain}
    started = await _call(plane, "POST", "/v1/research", json=body)
    assert started.status_code == 201, started.text
    job = started.json()["job_id"]
    assert await jobs.run_job(tenant, UUID(job), jobs.GraphEngine(plane.app.state.container)) == (
        "started"
    )
    ((run_id,),) = _db("SELECT run_id FROM research_jobs WHERE id = %s", job)
    return job, run_id


def _store_fact(run_id: UUID, value: str) -> None:
    """What the worker's grading writes for one finding: a current public fact of the run and its
    evidence, in one transaction (a fact never commits without evidence)."""
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute(
            "WITH f AS (INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
            " fingerprint, product_grade, engine_grade, status, visibility, run_id, value_text,"
            " owner_user_id, claim_kind)"
            " SELECT r.tenant_id, r.account_id, 'jobs.job_postings_12_months.hiring',"
            " jsonb_build_object('value', %(v)s::text), md5(%(v)s::text), 'probable', 'B',"
            " 'current', 'public', r.id, %(v)s::text, 'priya-test', 'sourced'"
            " FROM account_runs r WHERE r.id = %(r)s RETURNING id, tenant_id)"
            " INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind, url,"
            " supporting_sentence, span_sha256, source_tier, evidence_date, owner_user_id)"
            " SELECT tenant_id, id, 'source', 'https://www.liveco.example/careers/1', %(v)s,"
            " encode(sha256(%(v)s::bytea), 'hex'), 'T1', now(), 'priya-test' FROM f",
            {"v": value, "r": run_id},
        )
        c.commit()


async def test_a_scoped_research_counts_only_the_rows_it_planned(world: dict[str, Any]) -> None:
    """A job-postings research works on three rows; the thirty it marks "not planned" are
    neither counted nor shown as reported (the screen said "33 of 33" for such a research)."""
    plane = _plane(world["a"], "priya-test", world["payloads"])
    domain = f"scoped-{uuid4().hex[:6]}.example"
    job, run_id = await _running(plane, world["a"], domain)
    # the claim wrote every checklist row; the pull then reports the thirty outside the job
    # postings scope "not planned" and finishes one of the three it planned
    jobs_rows = ("job_board_by_lane", "job_postings_12_months", "job_postings_read_classified")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "UPDATE account_runs SET run_input = run_input || jsonb_build_object('scope_rows',"
            " to_jsonb(%s::text[])) WHERE id = %s",
            (list(jobs_rows), run_id),
        )
        c.execute(
            "UPDATE coverage_rows SET status = 'blocked', reason_code = 'not_planned',"
            " proof = '{\"calls\": []}' WHERE run_id = %s AND row_key <> ALL(%s)",
            (run_id, list(jobs_rows)),
        )
        c.execute(
            "UPDATE coverage_rows SET status = 'done', proof = '{\"calls\": []}'"
            " WHERE run_id = %s AND row_key = %s",
            (run_id, jobs_rows[0]),
        )
    body = (await _call(plane, "GET", "/v1/research/live", params={"selected": job})).json()
    read = next(st for st in body["detail"]["steps"] if st["id"] == "read")
    assert (read["rows_done"], read["rows_total"]) == (1, 3), body["detail"]["steps"]


async def test_live_snapshot_shows_a_fact_as_soon_as_it_is_stored(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    domain = f"live-{uuid4().hex[:6]}.example"
    job, run_id = await _running(plane, world["a"], domain)

    first = await _call(plane, "GET", "/v1/research/live", params={"selected": job})
    assert first.status_code == 200, first.text
    body = first.json()
    item = next(i for i in body["running"] if i["job_id"] == job)
    assert item["company"] == "Liveco" and item["kind"] == "New research"
    assert item["can_stop"] is True and item["findings"] == 0 and item["steps_total"] == 8
    assert body["detail"]["job_id"] == job and body["detail"]["latest_findings"] == []

    _store_fact(run_id, "Liveco is hiring four ServiceNow developers.")
    after = (await _call(plane, "GET", "/v1/research/live", params={"selected": job})).json()
    item = next(i for i in after["running"] if i["job_id"] == job)
    assert item["findings"] == 1
    (finding,) = after["detail"]["latest_findings"]
    assert finding["value"] == "Liveco is hiring four ServiceNow developers."
    assert finding["kind"] == "Job postings" and finding["source"] == "liveco.example"
    assert after["detail"]["findings_by_kind"] == [{"kind": "Job postings", "count": 1}]

    # Stop from the same screen: the run ends cancelled and the research moves to Recent
    stopped = await _call(plane, "POST", f"/v1/research/{job}/stop")
    assert stopped.status_code == 200, stopped.text
    ended = (await _call(plane, "GET", "/v1/research/live")).json()
    assert all(i["job_id"] != job for i in ended["running"])
    gone = next(i for i in ended["recent"] if i["job_id"] == job)
    assert gone["can_stop"] is False and gone["findings"] == 1  # what it found stays


async def test_an_empty_selection_is_none_and_a_bad_one_is_refused(world: dict[str, Any]) -> None:
    plane = _plane(world["a"], "priya-test", world["payloads"])
    assert (await _call(plane, "GET", "/v1/research/live?selected=")).status_code == 200
    assert (await _call(plane, "GET", "/v1/research/live?selected=x")).status_code == 422


async def test_live_shows_only_this_clients_web_research(world: dict[str, Any]) -> None:
    mine = _plane(world["a"], "priya-test", world["payloads"])
    job, _ = await _running(mine, world["a"], f"own-{uuid4().hex[:6]}.example")
    other = _plane(world["b"], "other-test", world["payloads"])
    body = (await _call(other, "GET", "/v1/research/live")).json()
    assert all(i["job_id"] != job for i in [*body["running"], *body["recent"]])
    assert body["detail"] is None or body["detail"]["job_id"] != job


async def test_live_stream_sends_a_new_snapshot_when_a_fact_is_stored(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from account_intelligence.replica import live

    fast = live.LiveSettings(poll_s=0.2, max_duration_s=2.5, heartbeat_s=10)
    monkeypatch.setattr(live, "load_settings", lambda: fast)
    plane = _plane(world["a"], "priya-test", world["payloads"])
    job, run_id = await _running(plane, world["a"], f"stream-{uuid4().hex[:6]}.example")

    async def store_later() -> None:
        await asyncio.sleep(0.7)
        await asyncio.to_thread(_store_fact, run_id, "Liveco opened a data platform team.")

    writer = asyncio.create_task(store_later())
    r = await _call(plane, "GET", "/v1/research/live/stream", params={"selected": job})
    await writer
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.text.startswith("retry: 200")  # the browser reconnects within the poll interval
    snapshots = [
        json.loads(block.split("data: ", 1)[1])
        for block in r.text.split("\n\n")
        if block.startswith("id: ") and "event: snapshot" in block
    ]
    assert len(snapshots) >= 2  # the first paint, then the change; never a repeat
    assert snapshots[0]["detail"]["latest_findings"] == []
    assert snapshots[-1]["detail"]["latest_findings"][0]["value"] == (
        "Liveco opened a data platform team."
    )
    tags = [b.split("\n", 1)[0] for b in r.text.split("\n\n") if b.startswith("id: ")]
    assert len(tags) == len(set(tags))
    assert r.text.rstrip().endswith("data: {}")  # the reconnect hint at the duration cap


async def test_with_nothing_running_the_stream_ends_and_asks_again_later(
    world: dict[str, Any],
) -> None:
    """An open stream keeps an instance awake: with no research running there is nothing to
    follow, so one snapshot is sent and the browser asks again after ``idle_retry_s``."""
    from tests.runs.test_lifecycle_db import tenant

    quiet = tenant("2100")
    plane = _plane(quiet, "quiet-test", world["payloads"])
    r = await asyncio.wait_for(_call(plane, "GET", "/v1/research/live/stream"), timeout=10)
    assert r.status_code == 200, r.text
    blocks = [b for b in r.text.split("\n\n") if b]
    assert blocks[0] == "retry: 1000" and blocks[-1] == "retry: 5000"
    assert sum("event: snapshot" in b for b in blocks) == 1


async def test_tokens_count_the_research_graphs_model_calls(world: dict[str, Any]) -> None:
    """The research graph logs its model calls in model_reply_log (the router's llm_calls stays
    empty while the router is off): the run's token meter and the live screen count them."""
    plane = _plane(world["a"], "priya-test", world["payloads"])
    job, run_id = await _running(plane, world["a"], f"tok-{uuid4().hex[:6]}.example")
    _db(
        "INSERT INTO model_reply_log (tenant_id, run_id, cache_key, task_kind, tier, model,"
        " served_from, input_tokens, output_tokens)"
        " SELECT tenant_id, id, 'k1', 'abm_pass2_extract', 'research', 'm', 'model', 1200, 300"
        " FROM account_runs WHERE id = %s",
        run_id,
    )
    run = await _call(plane, "GET", f"/v1/runs/{run_id}")
    assert run.status_code == 200, run.text
    assert run.json()["tokens_used"] == 1500
    body = (await _call(plane, "GET", "/v1/research/live", params={"selected": job})).json()
    assert next(i for i in body["running"] if i["job_id"] == job)["tokens"] == 1500
    assert body["detail"]["tokens"] == 1500
