"""ABM agent layer on the production adapters, end to end (SYNTHETIC only).

The real runner (``execute_slice`` with ``AbmGraphFactory``), connector ledger and budget gate,
grading engine and its single write path, tracker, credit service, payload store and lifecycle
run on the scratch Postgres; providers answer with the recorded cassette shapes
(``tests.system.world``) and the model is a scripted legacy port (no network, no spend).

Lying scenarios: an extractor that fabricates quotes and grades, a run without a pull to build
on, a tenant whose flag is off, another tenant reading the run's manifest.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.checkpoint import checkpointer_factory
from account_intelligence.platform.hermetic import InProcessScheduler, ScriptedLlm
from account_intelligence.runs.lifecycle import RunLimits, RunRequest, create_run
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.runner import execute_slice
from account_intelligence.workflow.spec import load_spec
from agents.adapters import PgManifestStore
from agents.production.wiring import AbmGraphFactory, uses_abm
from tests.connectors.conftest import Harness
from tests.system.world import (
    CANARY,
    DOMAIN,
    FAST_RETRY,
    SYNTHETIC_BALANCES,
    admin,
    install,
    tenant,
)
from tests.tracker.world import tracker

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
AGENT = re.compile(r"<!-- agent: ([a-z_0-9]+);")


def _sentence(text_: str) -> str:
    match = re.search(r"[^.!?\n]+[.!?]", text_)
    return (match.group() if match else text_).strip()


@dataclass
class AbmScript:
    """The scripted model by agent (read from the prompt header): honest unless ``lie``."""

    lie: bool = False
    calls: dict[str, int] = field(default_factory=dict)

    def __call__(self, tier: str, system: str, user: str) -> str:
        match = AGENT.search(system)
        agent = match.group(1) if match else "unknown"
        self.calls[agent] = self.calls.get(agent, 0) + 1
        data = json.loads(user)
        if agent == "setup":
            page = next((p for p in data["visited_pages"] if "careers" in p["url"]), None)
            return json.dumps({"careers_page_id": page["page_id"] if page else None})
        if agent in ("pass1", "pass2"):
            out = []
            for p in data["payloads"][:2]:
                sentence = _sentence(p["text"])
                if len(sentence) < 8:
                    continue
                candidate: dict[str, Any] = {
                    "row_key": p["row_key"],
                    "attribute": "signal",
                    "value": " ".join(sentence.split()[:3]),
                    "origin": "read",
                    "evidence": [{"payload_ref": p["payload_ref"], "quote": sentence}],
                }
                if self.lie:
                    candidate["evidence"] = [
                        {"payload_ref": p["payload_ref"], "quote": "Acme doubled revenue in 2026."}
                    ]
                out.append(candidate)
            if self.lie and data["payloads"]:
                p = data["payloads"][0]
                out.append(
                    {
                        "row_key": p["row_key"],
                        "attribute": "x",
                        "value": CANARY,
                        "origin": "read",
                        "grade": "confirmed",
                        "evidence": [{"payload_ref": p["payload_ref"], "quote": CANARY}],
                    }
                )
            return json.dumps({"candidates": out})
        if agent == "people":
            return json.dumps({"people": []})
        if agent == "sense_check":
            return json.dumps({"findings": [], "proposals": []})
        if agent == "distill":
            facts = data["facts"][:2]
            return json.dumps(
                {
                    "hypotheses": [
                        {
                            "text": "The account may be expanding its engineering base.",
                            "fact_ids": [f["fact_id"]],
                        }
                        for f in facts
                    ],
                    "questions": [
                        {
                            "text": "Which teams will the new office host?",
                            "fact_ids": [f["fact_id"]],
                        }
                        for f in facts
                    ],
                    "narrative": [],
                }
            )
        return "{}"


@dataclass
class AbmRun:
    tenant: UUID
    run: UUID
    script: AbmScript
    scheduler: InProcessScheduler


async def run_abm(
    harness: Harness,
    *,
    mode: str = "full",
    parent: UUID | None = None,
    tenant_id: UUID | None = None,
    script: AbmScript | None = None,
    key: str | None = None,
    auto_approve: bool = True,
    pursuing_client: str | None = "Synthetic Partner Co",
) -> AbmRun:
    """Create a run through the lifecycle, record its mode, and drive it to the end."""
    from tests.conftest import APP_DSN

    install(harness)
    script = script or AbmScript()
    t = tenant_id or tenant(auto_approve=auto_approve)
    async with tenant_txn(t) as s:
        created = await create_run(
            s,
            t,
            RunRequest(
                DOMAIN,
                "standard",
                key or f"abm-{mode}-{t}",
                {"mode": mode, **({"pursuing_client": pursuing_client} if pursuing_client else {})},
            ),
            RunLimits("v1", Decimal("20.00"), 400, Decimal("6.0")),
            tracker(s),
        )
        await s.execute(
            text(
                "UPDATE account_runs SET run_mode = :m, parent_run_id = :p,"
                " skill_config_reason = 'current' WHERE id = :r"
            ),
            {"m": mode, "p": parent, "r": created.run_id},
        )
    runtime = harness.runtime()
    spec = load_spec()
    llm = ScriptedLlm(script)

    async def deps_factory(tenant_id_: UUID, run_id: UUID) -> RunDeps:
        async with tenant_txn(tenant_id_) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT r.account_id, a.domain, r.run_input FROM account_runs r"
                        " JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                    ),
                    {"r": run_id},
                )
            ).one()
        return RunDeps(
            tenant_id=tenant_id_,
            run_id=run_id,
            account_id=row.account_id,
            domain=row.domain,
            txn=tenant_txn,
            llm=llm,
            payloads=harness.payloads,
            clock=harness.clock,
            spec=spec,
            connectors=runtime,
            canary=CANARY,
            extra_allowed_hosts=frozenset({DOMAIN, f"news.{DOMAIN}"}),
            run_input=dict(row.run_input or {}),
            balances=SYNTHETIC_BALANCES,
        )

    scheduler = InProcessScheduler()
    saver = checkpointer_factory(APP_DSN)

    async def run_slice(tenant_id_: UUID, run_id: UUID, slice_no: int) -> None:
        deps = await deps_factory(tenant_id_, run_id)
        factory = AbmGraphFactory() if await uses_abm(deps) else None
        await execute_slice(
            deps_factory,
            saver,
            scheduler,
            tenant_id_,
            run_id,
            slice_no,
            retry_policy=FAST_RETRY,
            graph_factory=factory,
        )

    scheduler.runner = run_slice
    await scheduler.enqueue_slice(t, created.run_id, 0)
    await scheduler.drain()
    return AbmRun(t, created.run_id, script, scheduler)


def execute(sql: str, *params: object) -> None:
    import psycopg

    from tests.tracker.world import admin_dsn

    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(sql, params)


def status(run: UUID) -> tuple[str, str | None]:
    row = admin("SELECT status, finished_reason FROM account_runs WHERE id = %s", run)[0]
    return str(row[0]), row[1]


def facts(run: UUID) -> list[tuple[Any, ...]]:
    return admin(
        "SELECT attribute_key, value_text, product_grade, visibility FROM facts WHERE run_id = %s"
        " AND superseded_at IS NULL ORDER BY attribute_key, value_text",
        run,
    )


def rows(run: UUID) -> dict[str, tuple[str | None, str | None]]:
    return {
        r[0]: (r[1], r[2])
        for r in admin(
            "SELECT row_key, status, reason_code FROM coverage_rows WHERE run_id = %s", run
        )
    }


def settled_once(run: UUID) -> bool:
    """The run settled, and no settlement entry is duplicated (commit and refund once each)."""
    entries = [
        e[0]
        for e in admin(
            "SELECT entry FROM wallet_ledger WHERE run_id = %s AND entry IN ('commit', 'refund')",
            run,
        )
    ]
    return bool(entries) and len(entries) == len(set(entries))


# Full run --------------------------------------------------------------------------------------


async def test_runner_executes_an_abm_run_end_to_end_on_postgres(harness: Harness) -> None:
    run = await run_abm(harness, mode="pull")  # a pull run: Phase 1 only, then check, then build
    st, reason = status(run.run)
    assert (st, reason) == ("PARTIAL", "pull_only")
    manifest = await PgManifestStore().for_run(run.tenant, run.run)
    assert manifest is not None and {r.row_key for r in manifest.rows} >= {"company_news"}
    stored = [r for r in manifest.rows if r.status == "stored"]
    assert stored and all(r.call_refs for r in stored)
    assert not facts(run.run) and not run.script.calls  # G5: no model, nothing interpreted
    spent = admin("SELECT spent_usd FROM account_runs WHERE id = %s", run.run)[0][0]
    ledger = admin(
        "SELECT COALESCE(SUM(charged_usd), 0) FROM tool_call_events WHERE run_id = %s"
        " AND event = 'done'",
        run.run,
    )[0][0]
    assert Decimal(spent) == Decimal(ledger) and settled_once(run.run)


async def test_full_abm_run_grades_through_the_engine_and_tracks_every_row(
    harness: Harness,
) -> None:
    t = tenant()
    execute(
        "INSERT INTO feature_flags (flag, version, rule, created_by, created_at) VALUES"
        " ('abm_agents', 1, %s, 'test', now())",
        json.dumps({"tenants": [str(t)]}),
    )
    run = await run_abm(harness, mode="full", tenant_id=t)
    st, reason = status(run.run)
    assert st == "PARTIAL" and reason == "builders_not_configured"  # no builder in this world
    graded = facts(run.run)
    drops = admin(
        "SELECT reason_code, count(*) FROM claim_drops WHERE run_id = %s GROUP BY reason_code",
        run.run,
    )
    assert graded, f"the engine recorded facts (drops: {drops}, model: {run.script.calls})"
    assert all(g[2] in ("confirmed", "probable", "hypothesis", "lead") for g in graded), graded
    tracked = rows(run.run)
    assert len(tracked) == 33 and all(s is not None for s, _ in tracked.values()), tracked
    people = {
        k: v
        for k, v in tracked.items()
        if k
        in (
            "people_by_platform",
            "full_profiles_senior_people",
            "posts_priority_people",
            "key_contact_profiles_posts",
        )
    }
    # G8, D-97: no people call is made. The tracker derives statuses from evidence (T1): with no
    # call the row is Not done with its reason; Blocked needs a failed call (C39).
    assert all(v == ("not_done", "consent_required") for v in people.values()), people
    sense = admin("SELECT count(*) FROM abm_sense_checks WHERE run_id = %s", run.run)[0][0]
    assert sense >= 1  # G6: the sense check ran before the build was attempted
    distilled = admin("SELECT audience FROM abm_distill_outputs WHERE run_id = %s", run.run)
    assert len(distilled) == 2 and {d[0] for d in distilled} == {"client", "internal"}
    assert settled_once(run.run)


async def test_full_runs_stay_on_workflow_v1_unless_the_flag_is_on(harness: Harness) -> None:
    from tests.system.world import Script, start_run

    started = await start_run(harness, Script())
    await started.scheduler.drain()
    assert not admin("SELECT 1 FROM pull_manifests WHERE run_id = %s", started.run)
    assert not admin("SELECT 1 FROM abm_sense_checks WHERE run_id = %s", started.run)


async def test_phase_two_on_a_stored_pull_inherits_its_ledger(harness: Harness) -> None:
    t = tenant()
    pull = await run_abm(harness, mode="pull", tenant_id=t, key="p1")
    calls_after_pull = admin("SELECT count(*) FROM tool_calls")[0][0]
    build = await run_abm(harness, mode="build", parent=pull.run, tenant_id=t, key="b1")
    st, _ = status(build.run)
    assert st in ("PARTIAL", "COMPLETED")
    assert admin("SELECT count(*) FROM tool_calls WHERE run_id = %s", build.run)[0][0] == 0
    assert admin("SELECT count(*) FROM tool_calls")[0][0] == calls_after_pull  # G16: no Phase 1
    assert facts(build.run), "facts graded from the inherited pull"
    tracked = rows(build.run)
    assert len(tracked) == 33 and all(s is not None for s, _ in tracked.values())


async def test_lying_extractor_in_production_writes_nothing_fabricated(harness: Harness) -> None:
    t = tenant()
    pull = await run_abm(harness, mode="pull", tenant_id=t, key="p")
    build = await run_abm(
        harness, mode="build", parent=pull.run, tenant_id=t, key="b", script=AbmScript(lie=True)
    )
    texts = " ".join(f[1] or "" for f in facts(build.run))
    assert "doubled revenue" not in texts and CANARY not in texts
    assert not admin(
        "SELECT 1 FROM facts WHERE run_id = %s AND product_grade = 'confirmed'"
        " AND read_in_primary_this_run IS NOT TRUE",
        build.run,
    )


async def test_missing_pursuing_client_fails_before_any_spend(harness: Harness) -> None:
    run = await run_abm(harness, mode="pull", pursuing_client=None)
    assert status(run.run) == ("FAILED", "invalid_setup")
    assert not admin("SELECT 1 FROM tool_call_events WHERE run_id = %s", run.run)
    refund = admin("SELECT 1 FROM wallet_ledger WHERE run_id = %s AND entry = 'refund'", run.run)
    assert refund and settled_once(run.run)


async def test_another_tenant_cannot_read_the_manifest(harness: Harness) -> None:
    run = await run_abm(harness, mode="pull")
    other = tenant()
    assert await PgManifestStore().for_run(other, run.run) is None
