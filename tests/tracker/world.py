"""Synthetic tracker world on real Postgres: tenant, account, both run rows, ledger rows.

Ledger rows are written the way the connector writes them (``tool_calls`` done rows with
``record_count`` in the response meta; ``tool_call_events`` failed rows with the error class), so
the production ``LedgerCallAdapter`` is exercised, not a fake. All names are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.config import load_grading_config
from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.service import TrackerService
from account_intelligence.workflow.spec import load_spec


def admin_dsn() -> str:
    from tests.conftest import ADMIN_DSN

    return ADMIN_DSN


@dataclass(frozen=True)
class World:
    tenant: UUID
    account: UUID
    run: UUID
    domain: str


def make_world(status: str = "RUNNING", tenant: UUID | None = None) -> World:
    """A tenant (new unless given), an account and a run with one id in both run tables."""
    domain = f"synthetic-{uuid4().hex[:8]}.example.com"
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        if tenant is None:
            tenant = c.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", ("synthetic-tenant",)
            ).fetchone()[0]
        account = c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant, domain),
        ).fetchone()[0]
        run = uuid4()
        c.execute(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at) VALUES (%s, %s, %s, 'full', 'running', 'cfg', 100, now())",
            (run, tenant, account),
        )
        c.execute(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, graph_version,"
            " budget_json, reserved_credits, idempotency_key, budget_usd, tool_call_cap)"
            " VALUES (%s, %s, %s, %s, 'test', '{}', 1, %s, 5, 60)",
            (run, tenant, account, status, "k-" + uuid4().hex),
        )
    return World(tenant, account, run, domain)


def ledger_done(
    w: World,
    operation: str = "web_search",
    record_count: int | None = 1,
    queries: tuple[str, ...] = ("synthetic query",),
    error_class: str | None = None,
) -> str:
    """A done ledger row; returns its call reference (args_hash)."""
    ref = uuid4().hex + uuid4().hex
    meta = {
        "retrieved_at": "2026-10-01T00:00:00+00:00",
        "args": {"queries": list(queries)},
        "record_count": record_count,
        "error_class": error_class,
    }
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, response, cost_usd,"
            " provider_code, operation, cost_source) VALUES (%s, %s, %s, %s, %s, 0, 'p_search',"
            " %s, 'documented')",
            (w.tenant, w.run, f"p_search:{operation}", ref, json.dumps(meta), operation),
        )
    return ref


def ledger_failed(w: World, operation: str = "web_search", error: str = "RateLimited") -> str:
    """A failed attempt event; returns its call reference."""
    ref = uuid4().hex + uuid4().hex
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event, owner,"
            " lease_epoch, provider_code, operation, charged_usd, error_class)"
            " VALUES (%s, %s, %s, 1, 'failed', 'w', 1, 'p_search', %s, 0, %s)",
            (w.tenant, w.run, ref, operation, error),
        )
    return ref


def tracker(session: AsyncSession) -> TrackerService:
    """The production service wiring on one tenant transaction."""
    spec = load_spec()
    return TrackerService(
        CoverageRepo(session),
        LedgerCallAdapter(session, load_grading_config().category_by_operation),
        RunInputAdapter(session),
        owners=spec.coverage_owners,
    )


@asynccontextmanager
async def txn(w: World) -> AsyncIterator[TrackerService]:
    """One tenant transaction with a tracker on it (commits on success)."""
    async with tenant_txn(w.tenant) as session:
        yield tracker(session)


def fetch(sql: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        return list(c.execute(sql, params).fetchall())
