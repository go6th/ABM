"""tests/isolation/test_rls.py - the hard CI gate. Fixtures create tenants A and B as the migrator."""

from uuid import UUID

import psycopg
import pytest
from sqlalchemy import text

from src.db.session import SessionLocal, tenant_txn

pytestmark = pytest.mark.asyncio


async def test_cross_tenant_read_returns_nothing(tenant_a: UUID, run_of_b: UUID):
    async with tenant_txn(tenant_a) as s:
        rows = (await s.execute(text("SELECT 1 FROM account_runs WHERE id = :i"), {"i": run_of_b})).all()
    assert rows == []


async def test_unset_tenant_fails_closed():
    async with SessionLocal() as s:  # no set_config at all
        n = (await s.execute(text("SELECT count(*) FROM account_runs"))).scalar_one()
    assert n == 0


async def test_cross_tenant_write_is_rejected(tenant_a: UUID, tenant_b: UUID, account_of_b: UUID):
    with pytest.raises(Exception, match="row-level security"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text(
                    "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json, "
                    "reserved_credits, idempotency_key) VALUES (:t, :a, 'x', '{}', 1, 'k-12345678')"
                ),
                {"t": tenant_b, "a": account_of_b},
            )


async def test_context_does_not_leak_through_pool(tenant_a: UUID):
    async with tenant_txn(tenant_a):
        pass
    async with SessionLocal() as s:  # very likely the same pooled connection
        v = (await s.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar_one()
    assert v in (None, "")


async def test_checkpoint_prefix_policy(tenant_a_dsn: str, tenant_b: UUID):
    async with await psycopg.AsyncConnection.connect(tenant_a_dsn, autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await c.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES (%s, '', 'c1', '{}', '{}')",
                (f"{tenant_b}:some-run",),
            )
