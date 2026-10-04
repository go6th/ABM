"""Isolation of the job pipeline tables (spec build_job_posting_pipeline.md section 6).

Catalog checks on every job table (forced RLS, a policy scoped TO app_rw with USING and WITH
CHECK, no UPDATE or DELETE grant to app_rw) and a cross-tenant read and write through
``tenant_txn``. The row-level round trips live in ``tests/jobs/test_repo_db.py``.
"""

from uuid import UUID

import psycopg
import pytest
from psycopg import errors
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from tests.conftest import ADMIN_DSN, APP_DSN

pytestmark = pytest.mark.asyncio

JOB_TABLES = (
    "job_postings",
    "job_posting_platform_sentences",
    "job_posting_classifications",
    "job_posting_observations",
    "job_pipeline_runs",
    "job_pipeline_passes",
)


async def test_every_job_table_is_forced_rls_and_insert_only_for_app_rw() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        for table in JOB_TABLES:
            enabled, forced = c.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s",
                (table,),
            ).fetchone()
            assert enabled and forced, table
            policies = c.execute(
                "SELECT roles::text[], qual IS NOT NULL, with_check IS NOT NULL"
                " FROM pg_policies WHERE tablename = %s",
                (table,),
            ).fetchall()
            assert policies and all(r == ["app_rw"] and u and k for r, u, k in policies), table
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                granted = c.execute(
                    "SELECT has_table_privilege('app_rw', %s, %s)", (table, privilege)
                ).fetchone()[0]
                assert not granted, (table, privilege)
            assert c.execute(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_name = %s AND column_name = 'tenant_id'",
                (table,),
            ).fetchone() == ("NO",), table


async def test_a_tenant_sees_and_writes_no_other_tenants_pipeline_passes(
    tenant_a: UUID, tenant_b: UUID, run_of_b: UUID, account_of_b: UUID
) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO job_pipeline_runs (run_id, tenant_id, account_id, as_of, window_start,"
            " config_hash, record_json) VALUES (%s, %s, %s, '2026-10-01', '2025-10-01', 'h', '{}')",
            (run_of_b, tenant_b, account_of_b),
        )
        c.execute(
            "INSERT INTO job_pipeline_passes (tenant_id, run_id, pass_no, gap_exit, record_json)"
            " VALUES (%s, %s, 1, 'converged', '{}')",
            (tenant_b, run_of_b),
        )
    async with tenant_txn(tenant_a) as s:
        for table in ("job_pipeline_runs", "job_pipeline_passes"):
            seen = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE run_id = :r"), {"r": run_of_b}
                )
            ).scalar_one()
            assert seen == 0, table
    # WITH CHECK refuses a foreign tenant id
    with (
        pytest.raises(errors.InsufficientPrivilege),
        psycopg.connect(APP_DSN) as c,
        c.transaction(),
    ):
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_a),))
        c.execute(
            "INSERT INTO job_pipeline_passes (tenant_id, run_id, pass_no, gap_exit,"
            " record_json) VALUES (%s, %s, 2, 'converged', '{}')",
            (tenant_b, run_of_b),
        )
