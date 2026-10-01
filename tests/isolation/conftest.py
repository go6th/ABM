"""Fixtures for the isolation suite.

The suite talks to a live Postgres created by migrations (owner = postgres, runtime =
app_rw). Tenant/account/run fixtures are inserted by the migrator DSN (fixture-only
bypass; the tests themselves always run as app_rw through tenant_txn).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from abm_generator.db.session import (
    dispose_engine,
    get_sessionmaker,
    tenant_txn,
)

MIGRATOR_DSN = os.environ.get("TEST_MIGRATOR_DSN", "postgresql://postgres:dev@localhost:5432/ai")
TENANT_A_DSN = os.environ.get(
    "TEST_TENANT_A_DSN", "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
)
TENANT_B_DSN = os.environ.get(
    "TEST_TENANT_B_DSN", "postgresql://app_rw:app_rw_dev@localhost:5432/ai"
)


def _migrator_connect() -> psycopg.Connection[dict]:
    return psycopg.connect(MIGRATOR_DSN, autocommit=True, row_factory=psycopg.rows.dict_row)


@pytest.fixture(scope="session", autouse=True)
def _require_migrated_db() -> None:
    """Fail fast (not skip) if migrations have not been applied."""
    try:
        with _migrator_connect() as conn:
            row = conn.execute(
                "SELECT to_regclass('public.account_runs') IS NOT NULL AS ok"
            ).fetchone()
    except psycopg.OperationalError as exc:
        pytest.fail(f"live Postgres not reachable for isolation suite: {exc}")
        return
    assert row is not None and row["ok"], (
        "isolation suite requires migrations; run: make db-up && make migrate"
    )


@pytest.fixture()
async def cleanup_engine() -> AsyncIterator[None]:
    """Dispose the pooled engine after each test so pool-leak assertions are exact."""
    yield
    await dispose_engine()


def _seed(tenant_a: UUID, tenant_b: UUID) -> dict[str, UUID]:
    """Insert two tenants, one account + run each; returns their ids (owner connection)."""
    ids: dict[str, UUID] = {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "account_a": uuid4(),
        "account_b": uuid4(),
        "run_a": uuid4(),
        "run_b": uuid4(),
    }
    with _migrator_connect() as conn:
        for key, tenant in (("tenant_a", tenant_a), ("tenant_b", tenant_b)):
            conn.execute(
                "INSERT INTO tenants (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (tenant, f"tenant-{key}"),
            )
            conn.execute(
                "INSERT INTO wallets (tenant_id, balance) VALUES (%s, %s)"
                " ON CONFLICT (tenant_id) DO NOTHING",
                (tenant, "1000"),
            )
        conn.execute(
            "INSERT INTO accounts (id, tenant_id, domain) VALUES (%s, %s, %s)",
            (ids["account_a"], tenant_a, "a.example.com"),
        )
        conn.execute(
            "INSERT INTO accounts (id, tenant_id, domain) VALUES (%s, %s, %s)",
            (ids["account_b"], tenant_b, "b.example.com"),
        )
        conn.execute(
            """INSERT INTO account_runs
               (id, tenant_id, account_id, status, graph_version, budget_json,
                reserved_credits, idempotency_key)
               VALUES (%s, %s, %s, 'QUEUED', 'test-1', '{}', 5, %s)""",
            (ids["run_a"], tenant_a, ids["account_a"], f"idem-{ids['run_a']}"),
        )
        conn.execute(
            """INSERT INTO account_runs
               (id, tenant_id, account_id, status, graph_version, budget_json,
                reserved_credits, idempotency_key)
               VALUES (%s, %s, %s, 'QUEUED', 'test-1', '{}', 5, %s)""",
            (ids["run_b"], tenant_b, ids["account_b"], f"idem-{ids['run_b']}"),
        )
    return ids


@pytest.fixture()
def seeded(tenant_a_id: UUID, tenant_b_id: UUID) -> dict[str, UUID]:
    """Seed two tenants with accounts and runs; ids keyed by role."""
    ids = _seed(tenant_a_id, tenant_b_id)
    yield ids
    with _migrator_connect() as conn:
        conn.execute(
            "DELETE FROM account_runs WHERE tenant_id IN (%s, %s)", (tenant_a_id, tenant_b_id)
        )
        conn.execute("DELETE FROM accounts WHERE tenant_id IN (%s, %s)", (tenant_a_id, tenant_b_id))
        conn.execute(
            "DELETE FROM wallet_ledger WHERE tenant_id IN (%s, %s)",
            (tenant_a_id, tenant_b_id),
        )
        conn.execute("DELETE FROM wallets WHERE tenant_id IN (%s, %s)", (tenant_a_id, tenant_b_id))
        conn.execute("DELETE FROM tenants WHERE id IN (%s, %s)", (tenant_a_id, tenant_b_id))


async def _count_runs(tenant: UUID) -> int:
    async with tenant_txn(tenant) as session:
        n = (await session.execute(text("SELECT count(*) FROM account_runs"))).scalar_one()
    return int(n)


@pytest.fixture()
async def run_of_b(seeded: dict[str, UUID]) -> UUID:
    """Return run id belonging to tenant B (used by cross-tenant read tests)."""
    return seeded["run_b"]


@pytest.fixture()
async def account_of_b(seeded: dict[str, UUID]) -> UUID:
    return seeded["account_b"]


@pytest.fixture()
async def session_a(tenant_a_id: UUID) -> AsyncIterator[AsyncSession]:
    """A tenant-scoped session for tenant A."""
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_a_id)},
        )
        yield session
