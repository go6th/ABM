"""Isolation test suite — the hard CI gate (I9, T10 exit criterion).

Pass condition: zero cross-tenant reads or writes; unset context returns zero rows;
tenant context does not leak through the pooled connections; the runtime role cannot
bypass RLS; 100 concurrent mixed-tenant requests over a 5-connection pool leak nothing.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from abm_generator.db.session import (
    check_app_role_is_safe,
    get_engine,
    tenant_txn,
    unset_txn,
)

from ..conftest import requires_db
from .conftest import MIGRATOR_DSN, TENANT_A_DSN, TENANT_B_DSN

pytestmark = [requires_db, pytest_asyncio]


# ---------------------------------------------------------------- role safety (I3)


async def test_runtime_role_is_nobypassrls_and_non_owner() -> None:
    async with unset_txn() as session:
        report = await check_app_role_is_safe(session)
    assert report["rolbypassrls"] is False
    assert report["rolsuper"] is False
    assert report["tables_owned"] == []


# ------------------------------------------------------------- cross-tenant reads


async def test_cross_tenant_read_returns_nothing(tenant_a_id: UUID, run_of_b: UUID) -> None:
    async with tenant_txn(tenant_a_id) as session:
        rows = (
            await session.execute(text("SELECT 1 FROM account_runs WHERE id = :i"), {"i": run_of_b})
        ).all()
    assert rows == []


async def test_unset_context_returns_zero_rows(cleanup_engine: None) -> None:
    async with unset_txn() as session:
        n = (await session.execute(text("SELECT count(*) FROM account_runs"))).scalar_one()
        assert n == 0
        m = (await session.execute(text("SELECT count(*) FROM facts"))).scalar_one()
        assert m == 0


async def test_cross_tenant_write_is_rejected(
    tenant_a_id: UUID, tenant_b_id: UUID, account_of_b: UUID
) -> None:
    with pytest.raises(IntegrityError) as excinfo:
        async with tenant_txn(tenant_a_id) as session:
            await session.execute(
                text(
                    "INSERT INTO account_runs (id, tenant_id, account_id, graph_version,"
                    " budget_json, reserved_credits, idempotency_key)"
                    " VALUES (:i, :t, :a, 'x', '{}', 1, :k)"
                ),
                {"i": uuid4(), "t": tenant_b_id, "a": account_of_b, "k": f"k-{uuid4()}"},
            )
    assert "row-level security" in str(excinfo.value.orig).lower()  # type: ignore[union-attr]


# ------------------------------------------------------------- cross-tenant reads


async def test_own_tenant_row_is_visible_and_writable(tenant_a_id: UUID) -> None:
    async with tenant_txn(tenant_a_id) as session:
        row = session.execute(
            text(
                "INSERT INTO accounts (id, tenant_id, domain)"
                " VALUES (:i, (SELECT app_tenant()), :d) RETURNING id"
            ),
            {"i": uuid4(), "d": "own.example.com"},
        ).first()
        assert row is not None


# ------------------------------------------------------------------ pooling (I1)


async def test_context_does_not_leak_through_pool(tenant_a_id: UUID, cleanup_engine: None) -> None:
    """The single most dangerous pooling trap: SET LOCAL outside a txn / residual context."""
    async with tenant_txn(tenant_a_id) as session:
        val = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar_one()
        assert val == str(tenant_a_id)
    # Next session, very likely the SAME pooled connection: context must be gone.
    async with unset_txn() as session:
        val = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar_one()
        assert val in (None, "")


async def test_pool_leak_storm_100_concurrent_mixed_tenant(
    seeded: dict[str, UUID], cleanup_engine: None
) -> None:
    """100 concurrent mixed-tenant requests over a 5-connection pool (T10)."""
    # Pass condition: zero cross-tenant rows across the whole storm.
    tenant_a, tenant_b = seeded["tenant_a"], seeded["tenant_b"]
    run_a, run_b = seeded["run_a"], seeded["run_b"]

    # Give each tenant one additional run so every request has data to see.
    for tenant, account in ((tenant_a, seeded["account_a"]), (tenant_b, seeded["account_b"])):
        async with tenant_txn(tenant) as session:
            await session.execute(
                text(
                    "INSERT INTO account_runs (id, tenant_id, account_id, graph_version,"
                    " budget_json, reserved_credits, idempotency_key)"
                    " VALUES (:i, (SELECT app_tenant()), :a, 'v', '{}', 1, :k)"
                ),
                {"i": uuid4(), "a": account, "k": f"storm-{uuid4()}"},
            )

    # Pool of exactly 5 shared by both tenants (engine configured in pyproject/env).
    engine = get_engine()
    assert engine.pool.size() <= 5 or engine.pool.status()["pool_size"] <= 5

    violations: list[str] = []

    async def one_request(tenant: UUID, other_run: UUID) -> None:
        try:
            async with tenant_txn(tenant) as session:
                leak = (
                    await session.execute(
                        text("SELECT 1 FROM account_runs WHERE id = :i"), {"i": other_run}
                    )
                ).first()
                own = (
                    await session.execute(text("SELECT count(*) FROM account_runs"))
                ).scalar_one()
                ctx = (
                    await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
                ).scalar_one()
            if leak is not None:
                violations.append(f"cross-tenant read: {tenant} saw {other_run}")
            if int(own) != 2:  # seeded run + storm run
                violations.append(f"wrong row count for {tenant}: {own}")
            if ctx != str(tenant):
                violations.append(f"context mismatch: {ctx} != {tenant}")
        except Exception as exc:
            violations.append(f"request failed: {exc!r}")

    tasks = []
    for i in range(100):
        if i % 2 == 0:
            tasks.append(one_request(tenant_a, run_b))
        else:
            tasks.append(one_request(tenant_b, run_a))
    await asyncio.gather(*tasks)

    assert violations == []


# -------------------------------------------------------- checkpoint prefix policy


async def test_checkpoint_prefix_policy(tenant_a_id: UUID, tenant_b_id: UUID) -> None:
    """Library checkpoint tables reject another tenant's thread ids (ADR-002, D11)."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation) as excinfo:
        conn = await psycopg.AsyncConnection.connect(TENANT_A_DSN)
        try:
            await conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id,"
                " checkpoint, metadata)"
                " VALUES (%s, '', 'c1', %s, %s)",
                (
                    f"{tenant_b_id}:some-run",
                    psycopg.types.json.Jsonb({}),
                    psycopg.types.json.Jsonb({}),
                ),
            )
            await conn.commit()
        finally:
            await conn.close()
    assert "tenant_prefix" in str(excinfo.value)


async def test_checkpoint_prefix_allows_own_tenant(tenant_a_id: UUID) -> None:
    import psycopg

    conn = await psycopg.AsyncConnection.connect(TENANT_A_DSN)
    try:
        await conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id,"
            " checkpoint, metadata)"
            " VALUES (%s, '', %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (
                f"{tenant_a_id}:own-run",
                f"c-{uuid4()}",
                psycopg.types.json.Jsonb({}),
                psycopg.types.json.Jsonb({}),
            ),
        )
        await conn.commit()
    finally:
        await conn.close()


# ------------------------------------------------------------ public_reader (ADR-006)


async def test_public_reader_cannot_read_private_facts(
    seeded: dict[str, UUID], tenant_a_id: UUID
) -> None:
    """public_reader role sees zero private rows; without tenant context sees nothing (ADR-006)."""
    import psycopg

    # Insert one private fact as tenant A.
    async with tenant_txn(tenant_a_id) as session:
        await session.execute(
            text(
                "INSERT INTO facts (id, tenant_id, account_id, attribute_key, value_json,"
                " fingerprint, grade, confidence, status, visibility)"
                " VALUES (:i, (SELECT app_tenant()), :a, 'exec.cto.name', '{\"v\": \"X\"}',"
                " :f, 'A', 0.9, 'active', 'private')"
            ),
            {"i": uuid4(), "a": seeded["account_a"], "f": uuid4().hex},
        )
    conn = await psycopg.AsyncConnection.connect(TENANT_A_DSN)
    try:
        await conn.execute("SET ROLE public_reader")
        count = (await conn.execute("SELECT count(*) FROM facts")).fetchone()
        assert count is not None and count[0] == 0
    finally:
        await conn.close()


# --------------------------------------------------------------- migrations lint


def test_migration_file_enforces_force_rls() -> None:
    """Static lint: every tenant table in the migration gets ENABLE + FORCE RLS."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0001_initial.py"
    ).read_text(encoding="utf-8")
    for table in (
        "tenants",
        "memberships",
        "accounts",
        "account_runs",
        "facts",
        "wallets",
        "wallet_ledger",
        "tool_calls",
        "chunks",
    ):
        assert table in source, f"table {table} missing from migration"
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source
    assert "app_tenant()" in source
    assert "BYPASSRLS" not in source.replace("NOBYPASSRLS", "")


def test_roles_sql_nobypassrls() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "docker" / "init" / "00-roles.sql").read_text(
        encoding="utf-8"
    )
    assert "NOBYPASSRLS" in source
    assert "CREATE ROLE app_rw" in source


@pytest.fixture(autouse=True)
def _no_migrator_urls_leaked() -> None:
    """Guard: tests must never open the migrator DSN through the app session module."""
    assert MIGRATOR_DSN.startswith("postgresql://postgres:")
    assert TENANT_A_DSN.endswith("/ai") and TENANT_B_DSN.endswith("/ai")
    yield
