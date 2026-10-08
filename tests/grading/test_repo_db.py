"""The database mirrors the grade rules for the runtime role, and the write path is atomic and
idempotent (real Postgres, forced RLS, app_rw)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading import repo
from account_intelligence.grading.config import GradingConfig
from account_intelligence.grading.leads import new_lead, submit_query
from account_intelligence.grading.pipeline import Dropped
from tests.conftest import ADMIN_DSN
from tests.connectors.conftest import make_run
from tests.grading.db_world import graded_confirmed, graded_lead, run_info

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
REPO = Path(__file__).resolve().parents[2]


def _admin_count(sql: str, *args: object) -> int:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        return int(c.execute(sql, args).fetchone()[0])  # type: ignore[index]


async def _raw_fact(
    tenant: UUID, account: UUID, grade: str, primary: bool, stated: bool, evidence: bool = True
) -> None:
    async with tenant_txn(tenant) as s:
        fid = (
            await s.execute(
                text(
                    "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                    " fingerprint, product_grade,"
                    " read_in_primary_this_run, may_state_to_prospect, confirming_question)"
                    " VALUES ((SELECT app_tenant()), :a, 'jobs.x.y',"
                    " '{}', 'fp', :g, :p, :m, :q) RETURNING id"
                ),
                # migration 0033: a hypothesis needs its confirming question, so the check under
                # test (evidence at commit, primary read) is the one that fires
                {
                    "a": account,
                    "g": grade,
                    "p": primary,
                    "m": stated,
                    "q": "Synthetic: what would confirm this?",
                },
            )
        ).scalar_one()
        if evidence:
            await s.execute(
                text(
                    "INSERT INTO fact_evidence (fact_id, evidence_kind, supporting_sentence,"
                    " call_ref)"
                    " VALUES (:f, 'search_proof', 'proof sentence', 'c1')"
                ),
                {"f": fid},
            )


async def test_db_refuses_confirmed_without_primary_read_for_app_rw(tenant_a: UUID) -> None:
    account, _run, _ = make_run(tenant_a)
    with pytest.raises(Exception, match="ck_facts_confirmed_needs_primary_read"):
        await _raw_fact(tenant_a, account, "confirmed", False, True)
    with pytest.raises(Exception, match="ck_facts_only_confirmed_may_be_stated"):
        await _raw_fact(tenant_a, account, "probable", False, True)
    with pytest.raises(Exception, match="ck_facts_product_grade_present"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text(
                    "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                    " fingerprint)"
                    " VALUES ((SELECT app_tenant()), :a, 'k.x', '{}', 'fp')"
                ),
                {"a": account},
            )
    await _raw_fact(tenant_a, account, "confirmed", True, True)  # the valid shape is accepted


async def test_fact_without_evidence_is_refused_at_commit_under_rls(tenant_a: UUID) -> None:
    account, _run, _ = make_run(tenant_a)
    with pytest.raises(Exception, match="has no evidence row at commit"):
        await _raw_fact(tenant_a, account, "hypothesis", False, False, evidence=False)
    assert _admin_count("SELECT count(*) FROM facts WHERE account_id = %s", account) == 0


async def test_record_graded_fact_is_atomic_and_idempotent(tenant_a: UUID) -> None:
    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    first = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    again = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    assert first == again
    assert _admin_count("SELECT count(*) FROM facts WHERE run_id = %s", run) == 1
    assert _admin_count("SELECT count(*) FROM fact_evidence WHERE fact_id = %s", first) == 1
    assert _admin_count("SELECT count(*) FROM grade_events WHERE fact_id = %s", first) == 1
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT product_grade, read_in_primary_this_run, may_state_to_prospect, visibility,"
            " fingerprint"
            " FROM facts WHERE id = %s",
            (first,),
        ).fetchone()
    assert row[:4] == ("confirmed", True, True, "public") and row[4] == repo.fingerprint(
        account, "jobs.salesforce_admin.open", "Senior Salesforce Administrator"
    )


async def test_internal_facts_are_private_and_hidden_from_the_public_renderer(
    tenant_a: UUID,
) -> None:
    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    internal = graded_confirmed(
        claim_hash="hi",
        product_grade="internal",
        read_in_primary_this_run=False,
        may_state_to_prospect=False,
        precedence_rule="internal_only",
    )
    fid = await repo.record_graded_fact(tenant_txn, info, internal)
    pub = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    async with tenant_txn(tenant_a) as s:
        await s.execute(text("SET LOCAL ROLE public_reader"))
        seen = {r[0] for r in (await s.execute(text("SELECT fact_id FROM fact_evidence"))).all()}
    assert pub in seen and fid not in seen


async def test_supersede_writes_a_new_row_and_never_updates_the_value(tenant_a: UUID) -> None:
    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    old = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    downgraded = graded_confirmed(
        claim_hash="h1",
        product_grade="probable",
        may_state_to_prospect=False,
        reasoning="judge unavailable",
        flags=("unjudged",),
        engine_grade="C",
    )
    new = await repo.supersede(tenant_txn, info, old, downgraded, reason_code="judge_cap")
    with psycopg.connect(ADMIN_DSN) as c:
        rows = dict(
            c.execute("SELECT id, product_grade FROM facts WHERE run_id = %s", (run,)).fetchall()
        )
        ev = c.execute(
            "SELECT from_product, to_product, reason_code FROM grade_events WHERE fact_id = %s",
            (new,),
        ).fetchone()
        sup = c.execute("SELECT superseded_by FROM facts WHERE id = %s", (old,)).fetchone()[0]
    assert (
        rows == {old: "confirmed", new: "probable"}
        and sup == new
        and ev == ("confirmed", "probable", "judge_cap")
    )
    with pytest.raises(LookupError):
        await repo.supersede(tenant_txn, info, old, downgraded, reason_code="again")
    with pytest.raises(Exception, match="append-only"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text("UPDATE facts SET value_text = 'edited' WHERE id = :i"), {"i": new}
            )
    with pytest.raises(Exception, match="set once"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text("UPDATE facts SET superseded_at = now() - interval '1 day' WHERE id = :i"),
                {"i": old},
            )


async def test_event_tables_are_insert_only(tenant_a: UUID) -> None:
    for sql in (
        "UPDATE fact_evidence SET supporting_sentence = 'x'",
        "DELETE FROM fact_evidence",
        "UPDATE grade_events SET reason_code = 'x'",
        "DELETE FROM claim_drops",
        "UPDATE lead_verifications SET status = 'complete'",
        "DELETE FROM lead_verification_queries",
        "DELETE FROM facts",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with tenant_txn(tenant_a) as s:
                await s.execute(text(sql))


async def test_drops_are_counted_once_and_leads_are_snapshotted(
    tenant_a: UUID, cfg: GradingConfig
) -> None:
    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    drops = [
        Dropped("url_not_in_run", "h1", "x", "jobs.a.b", "excerpt"),
        Dropped("hostile_source", "h2", "y", None, "e", "https://x.example/a"),
    ]
    assert await repo.record_drops(tenant_txn, info, drops) == 2
    assert await repo.record_drops(tenant_txn, info, drops) == 0
    from tests.grading.world import _fetch

    f = {
        "c": _fetch(
            "c",
            "web_search",
            "search_snippet",
            "web_search",
            "p_search",
            queries=("salesforce admin",),
        )
    }
    state = submit_query(new_lead("jobs.a=x"), "salesforce admin", "web_search", "c", f, set(), cfg)
    lead_id = await repo.record_lead(tenant_txn, info, state)
    assert (
        _admin_count(
            "SELECT count(*) FROM lead_verification_queries WHERE lead_verification_id = %s",
            lead_id,
        )
        == 1
    )
    with pytest.raises(Exception, match="ck_lead_verifications_complete_rule"):
        await repo.record_lead(
            tenant_txn, info, state.__class__(**{**state.__dict__, "status": "complete"})
        )
    lead_fact = await repo.record_graded_fact(tenant_txn, info, graded_lead())
    assert (
        _admin_count(
            "SELECT count(*) FROM facts WHERE id = %s AND lead_status = 'pending'", lead_fact
        )
        == 1
    )


async def test_sql_lead_rule_numbers_equal_the_config(cfg: GradingConfig) -> None:
    sql = (REPO / "migrations" / "0009_grading.sql").read_text()
    rule = cfg.lead_rule
    assert (
        f"accepted_count >= {rule.min_distinct_queries} AND cardinality(categories) >="
        f" {rule.min_distinct_tool_categories}" in sql
    )


async def test_two_tenants_on_the_same_domain_never_cross(tenant_a: UUID, tenant_b: UUID) -> None:
    acc_a, run_a, _ = make_run(tenant_a, domain="same.example")
    acc_b, run_b, _ = make_run(tenant_b, domain="same.example")
    fa = await repo.record_graded_fact(
        tenant_txn, run_info(tenant_a, acc_a, run_a), graded_confirmed()
    )
    fb = await repo.record_graded_fact(
        tenant_txn, run_info(tenant_b, acc_b, run_b), graded_confirmed()
    )
    assert fa != fb
    async with tenant_txn(tenant_a) as s:
        seen = {
            r[0]
            for r in (await s.execute(text("SELECT id FROM facts WHERE run_id IS NOT NULL"))).all()
        }
        ev = {r[0] for r in (await s.execute(text("SELECT fact_id FROM fact_evidence"))).all()}
    assert fa in seen and fb not in seen and fb not in ev


def _kill(mode: str, tenant: UUID, account: UUID, run: UUID) -> int:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.grading.kill_fact_worker",
            mode,
            str(tenant),
            str(account),
            str(run),
        ],
        cwd=REPO,
        env=os.environ.copy(),
        capture_output=True,
        timeout=120,
    ).returncode


async def test_kill_mid_transaction_leaves_nothing_and_replay_writes_once(tenant_a: UUID) -> None:
    account, run, _ = make_run(tenant_a)
    assert _kill("mid_txn", tenant_a, account, run) == 137
    assert _admin_count("SELECT count(*) FROM facts WHERE run_id = %s", run) == 0
    assert _kill("after_commit", tenant_a, account, run) == 137
    replay = await repo.record_graded_fact(
        tenant_txn, run_info(tenant_a, account, run), graded_confirmed()
    )
    assert _admin_count("SELECT count(*) FROM facts WHERE run_id = %s", run) == 1
    assert _admin_count("SELECT count(*) FROM fact_evidence WHERE fact_id = %s", replay) == 1
    assert uuid4()


async def test_grading_config_hash_is_pinned_once_on_the_run(
    tenant_a: UUID, cfg: GradingConfig
) -> None:
    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    assert await repo.pin_config(tenant_txn, info, cfg.content_hash) == cfg.content_hash
    assert await repo.pin_config(tenant_txn, info, "f" * 64) == cfg.content_hash
    with pytest.raises(LookupError):
        await repo.pin_config(tenant_txn, run_info(tenant_a, account, uuid4()), cfg.content_hash)
