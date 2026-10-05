"""Additional isolation checks that extend the architecture listing.

Cover the public-reader leak, tenant concurrency, insert-only ledgers,
chunk grants, and canary vectors.
"""

import asyncio
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


async def test_public_reader_cannot_read_private_facts(tenant_a: UUID, private_fact_of_a: UUID):
    async with tenant_txn(tenant_a) as s:
        own = (
            await s.execute(text("SELECT count(*) FROM facts WHERE visibility = 'private'"))
        ).scalar_one()
        await s.execute(text("SET LOCAL ROLE public_reader"))
        private = (
            await s.execute(text("SELECT count(*) FROM facts WHERE visibility = 'private'"))
        ).scalar_one()
        public = (
            await s.execute(text("SELECT count(*) FROM facts WHERE visibility = 'public'"))
        ).scalar_one()
    assert own == 1  # the tenant itself can see its private fact
    assert private == 0  # the public renderer role must not
    assert public == 1


async def test_public_reader_has_no_grant_on_chunks(tenant_a: UUID):
    with pytest.raises(Exception, match="permission denied"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(text("SET LOCAL ROLE public_reader"))
            await s.execute(text("SELECT count(*) FROM chunks"))


async def test_ledgers_are_insert_only(tenant_a: UUID):
    async with tenant_txn(tenant_a) as s:
        await s.execute(
            text(
                "INSERT INTO wallet_ledger (tenant_id, entry, amount, balance_after) "
                "VALUES ((SELECT app_tenant()), 'topup', 10, 110)"
            )
        )
    for stmt in (
        "UPDATE wallet_ledger SET amount = 0",
        "DELETE FROM wallet_ledger",
        "UPDATE tool_calls SET cost_usd = 0",
        "DELETE FROM tool_calls",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with tenant_txn(tenant_a) as s:
                await s.execute(text(stmt))


async def test_tenant_sees_only_itself(tenant_a: UUID, tenant_b: UUID):
    async with tenant_txn(tenant_a) as s:
        ids = {r.id for r in (await s.execute(text("SELECT id FROM tenants"))).all()}
    assert ids == {tenant_a}


async def test_100_concurrent_mixed_tenant_requests_over_5_connections(
    tenant_a: UUID, tenant_b: UUID
):
    """Spike 1 pass criterion: zero cross-tenant rows under pool pressure."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for t in (tenant_a, tenant_b):
            acct = c.execute(
                "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
                (t, f"{t.hex[:8]}.example.com"),
            ).fetchone()[0]
            for i in range(3):
                c.execute(
                    "INSERT INTO account_runs "
                    "(tenant_id, account_id, graph_version, budget_json, reserved_credits, "
                    "idempotency_key) VALUES (%s, %s, 'x', '{}', 1, %s)",
                    (t, acct, f"k-{t.hex[:6]}-{i}-xx"),
                )

    async def one(t: UUID) -> set[UUID]:
        async with tenant_txn(t) as s:
            await asyncio.sleep(0)  # let other tasks interleave on the pool
            return {
                r.tenant_id
                for r in (await s.execute(text("SELECT tenant_id FROM account_runs"))).all()
            }

    results = await asyncio.gather(*[one(tenant_a if i % 2 else tenant_b) for i in range(100)])
    for i, seen in enumerate(results):
        want = tenant_a if i % 2 else tenant_b
        assert seen == {want}, f"request {i} saw {seen}"


async def test_canary_vector_does_not_cross_tenants(tenant_a: UUID, tenant_b: UUID):
    vec = "[" + ",".join(["0.1"] * 1536) + "]"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        acct = c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant_b, f"canary-{tenant_b.hex[:6]}.example.com"),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO chunks (tenant_id, account_id, doc_id, content, embedding) "
            "VALUES (%s, %s, gen_random_uuid(), 'CANARY-TENANT-B', %s::halfvec)",
            (tenant_b, acct, vec),
        )
    async with tenant_txn(tenant_a) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT content FROM chunks ORDER BY embedding <=> CAST(:v AS halfvec) LIMIT 5"
                ),
                {"v": vec},
            )
        ).all()
    assert rows == []  # tenant A's nearest-neighbour search must not return tenant B's canary


async def test_direct_access_to_a_chunk_partition_is_denied(tenant_a: UUID):
    """Direct access to a child partition must be denied to app_rw (moved from the retired
    starter-graph integration test)."""
    from sqlalchemy import text

    from account_intelligence.db.session import tenant_txn

    with pytest.raises(Exception, match="permission denied"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(text("SELECT count(*) FROM chunks_p0"))
