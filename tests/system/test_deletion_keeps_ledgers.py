"""SD-01: a tenant deletion must never reach a ledger, billing or audit table through a foreign key.

``credit_entries`` (the credit reservations and settlements, kept for billing) hangs on
``research_jobs`` by a foreign key. Before migration 0116 that key was ``ON DELETE CASCADE`` and
the tenant plan erased ``research_jobs``, so executing an approved deletion would have silently
emptied the tenant's credit ledger. The plan now scrubs the client's words from ``research_jobs``
instead, and the key is ``RESTRICT``.

Two proofs: the schema check (no table the plan erases has a cascading or nulling foreign key into
a table the plan keeps, at any depth) and the behaviour (a tenant with ledger rows goes through the
whole approved deletion and its ledger rows and totals are unchanged).

GUARANTEE(test_tenant_deletion_keeps_the_credit_ledger_and_its_totals)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from tenancy import lifecycle
from tests.conftest import ADMIN_DSN
from tests.isolation.matrix.plant import plant, tenant_tables

pytestmark = pytest.mark.requires_postgres
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _tenant(name: str) -> UUID:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tid: UUID = c.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-synthetic",)
        ).fetchone()[0]
    return tid


@pytest.mark.asyncio
async def test_no_erased_table_cascades_into_a_kept_table() -> None:
    plan = lifecycle.load_plan()
    async with tenant_txn(_tenant("sd01-fk")) as s:
        breaches = await lifecycle.cascade_breaches(s, plan)
    assert breaches == [], breaches


def test_the_plan_check_refuses_a_cascade_into_a_kept_table() -> None:
    plan = lifecycle.load_plan()
    fks = [("credit_entries", "research_jobs", "c"), ("chat_turns", "chat_threads", "c")]
    poisoned = lifecycle.DeletionPlan(
        erase=(*plan.erase, "research_jobs"),
        blocked=plan.blocked,
        never=plan.never,
        batch_size=1,
        max_batches_per_pass=1,
    )
    assert lifecycle.foreign_key_breaches(poisoned, fks) == [
        "credit_entries (kept) loses rows when research_jobs (erased) is deleted"
    ]
    assert lifecycle.foreign_key_breaches(plan, fks) == []


def _ledger(t: UUID) -> tuple[int, int]:
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT count(*), coalesce(sum(credits), 0) FROM credit_entries WHERE tenant_id = %s",
            (t,),
        ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


@pytest.mark.asyncio
async def test_tenant_deletion_keeps_the_credit_ledger_and_its_totals() -> None:
    t, other = _tenant("sd01-a"), _tenant("sd01-b")
    jobs: list[Any] = []
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tables = {tb.name: tb for tb in tenant_tables(admin)}
        for tenant in (t, other):
            for n, kind in enumerate(("reserve", "settle")):
                job = plant(
                    admin,
                    tables["research_jobs"],
                    tenant,
                    {
                        "kind": "web_refresh",
                        "scope": "topic",
                        "status": "done",
                        "period": "2026-11",
                        "started_at": NOW,
                        "finished_at": NOW,
                        "credits_estimated": 250,
                        "credits_used": 180,
                        "requested_by": "synthetic",
                        "goal": "the client's own words",
                        "topic": "their hiring plans",
                        "failure_reason": f"free text {n}",
                    },
                )
                jobs.append((tenant, job["id"]))
                plant(
                    admin,
                    tables["credit_entries"],
                    tenant,
                    {
                        "job_id": job["id"],
                        "period": "2026-11",
                        "kind": kind,
                        "credits": 250 if kind == "reserve" else 180,
                        "purpose": "web_refresh",
                        "actor": "synthetic",
                    },
                )
    before, other_before = _ledger(t), _ledger(other)
    assert before == (2, 430)
    async with tenant_txn(t) as s:
        req = await lifecycle.request_deletion(
            s, actor="op-1", plane="operator", reason_code="tenant_request", now=NOW
        )
        report = await lifecycle.dry_run(s, req, actor="op-1", now=NOW)
        await lifecycle.approve(s, req, actor="op-2", now=NOW)
    reasons = []
    # LOOP(test): cap=50 passes; exit=completed or blocked
    for _ in range(50):
        async with tenant_txn(t) as s:
            out = await lifecycle.execute(s, req, actor="op-2", now=NOW)
        reasons.append(out.exit_reason)
        if out.exit_reason != "batch_cap":
            break
    assert reasons[-1] in ("completed", "blocked"), reasons
    # the ledger and its totals are exactly what they were, for this tenant and the other
    assert _ledger(t) == before
    assert _ledger(other) == other_before
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT tenant_id, goal, topic, failure_reason, credits_estimated, credits_used,"
            " status FROM research_jobs WHERE id = ANY(%s)",
            ([j for _, j in jobs],),
        ).fetchall()
    assert len(rows) == 4  # every ledger row keeps its parent
    assert "research_jobs" not in report["erase"]
    assert report["scrub"] == {"research_jobs": 2}
    for tenant, goal, topic, failure, estimated, used, status in rows:
        assert (estimated, used, status) == (250, 180, "done")
        if tenant == t:
            assert (goal, topic, failure) == (None, "purged", None)
        else:
            assert goal == "the client's own words" and topic == "their hiring plans" and failure


def test_the_database_refuses_to_delete_a_job_that_carries_ledger_rows() -> None:
    t = _tenant("sd01-r")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tables = {tb.name: tb for tb in tenant_tables(admin)}
        job = plant(
            admin,
            tables["research_jobs"],
            t,
            {
                "kind": "new_research",
                "scope": "full",
                "status": "done",
                "period": "2026-11",
                "started_at": NOW,
                "finished_at": NOW,
            },
        )
        plant(
            admin,
            tables["credit_entries"],
            t,
            {
                "job_id": job["id"],
                "period": "2026-11",
                "kind": "reserve",
                "credits": 5,
                "purpose": "new_research",
                "actor": "synthetic",
            },
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            admin.execute("DELETE FROM research_jobs WHERE id = %s", (job["id"],))
    assert _ledger(t) == (1, 5)
