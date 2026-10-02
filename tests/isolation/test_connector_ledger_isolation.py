"""Hard-gate isolation checks for the connector ledger tables (migration 0007).

run_leases and tool_call_events carry tenant_id with forced RLS scoped TO app_rw: a
tenant cannot read, write into, or take over another tenant's rows, and the intent log
is insert-only.
"""

from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.asyncio


def _seed(tenant: UUID) -> UUID:
    run = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO run_leases (run_id, tenant_id, owner, epoch, expires_at, heartbeat_at)"
            " VALUES (%s, %s, 'w', 1, now() + interval '1 hour', now())",
            (run, tenant),
        )
        c.execute(
            "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event, owner,"
            " lease_epoch, reserved_usd) VALUES (%s, %s, 'h', 1, 'started', 'w', 1, 0.1)",
            (tenant, run),
        )
    return run


async def test_other_tenant_cannot_see_leases_or_intents(tenant_a: UUID, tenant_b: UUID) -> None:
    run = _seed(tenant_b)
    async with tenant_txn(tenant_a) as s:
        for table in ("run_leases", "tool_call_events"):
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE run_id = :r"), {"r": run})
            ).scalar_one()
            assert n == 0, table


async def test_cannot_write_rows_into_another_tenant(tenant_a: UUID, tenant_b: UUID) -> None:
    with pytest.raises(Exception, match="row-level security"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text(
                    "INSERT INTO tool_call_events (tenant_id, run_id, args_hash, attempt, event,"
                    " owner, lease_epoch, reserved_usd)"
                    " VALUES (:t, :r, 'h', 1, 'started', 'w', 1, 0)"
                ),
                {"t": tenant_b, "r": uuid4()},
            )
    with pytest.raises(Exception, match="row-level security"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text(
                    "INSERT INTO run_leases (run_id, tenant_id, owner, epoch, expires_at,"
                    " heartbeat_at)"
                    " VALUES (:r, :t, 'w', 1, now(), now())"
                ),
                {"t": tenant_b, "r": uuid4()},
            )


async def test_cannot_take_over_another_tenants_lease(tenant_a: UUID, tenant_b: UUID) -> None:
    run = _seed(tenant_b)
    async with tenant_txn(tenant_a) as s:
        result = await s.execute(
            text("UPDATE run_leases SET owner = 'thief', expires_at = now() WHERE run_id = :r"),
            {"r": run},
        )
        assert result.rowcount == 0  # type: ignore[attr-defined]


async def test_intent_log_is_insert_only(tenant_a: UUID) -> None:
    _seed(tenant_a)
    for sql in (
        "UPDATE tool_call_events SET charged_usd = 0",
        "DELETE FROM tool_call_events",
        "DELETE FROM run_leases",
    ):
        with pytest.raises(Exception, match="permission denied"):
            async with tenant_txn(tenant_a) as s:
                await s.execute(text(sql))
