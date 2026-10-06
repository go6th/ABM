"""Eval results on Postgres: round trip, baselines only from green runs, insert-only tables, and
tenant isolation of verifier findings, call records and eval rows."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from evalgate import store
from evalgate.gates import GateResult
from tests.evals.test_harness import PROV, _result

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_eval_store_round_trip_and_insert_only(tenant_a: UUID, tenant_b: UUID) -> None:
    from tests.conftest import APP_DSN

    r = _result()
    red = await store.record(tenant_txn, tenant_a, r, GateResult("block", ("x",)), PROV, 1)
    with pytest.raises(ValueError):
        await store.promote_baseline(tenant_txn, tenant_a, red, "main", PROV.code_sha)
    green = await store.record(tenant_txn, tenant_a, r, GateResult("pass", ()), PROV, 1)
    await store.promote_baseline(tenant_txn, tenant_a, green, "main", PROV.code_sha)
    got = await store.latest_baseline(tenant_txn, tenant_a, "main")
    assert got is not None and got[0].metrics == r.metrics and got[1] == PROV
    newer = await store.record(
        tenant_txn, tenant_a, r, GateResult("pass", ()), replace(PROV, code_sha="e" * 40), 2
    )
    await store.promote_baseline(tenant_txn, tenant_a, newer, "main", "e" * 40)
    latest = await store.latest_baseline(tenant_txn, tenant_a, "main")
    assert latest is not None and latest[1].code_sha == "e" * 40  # a new row, the old one kept
    assert await store.latest_baseline(tenant_txn, tenant_b, "main") is None  # RLS
    with psycopg.connect(APP_DSN, autocommit=True) as c:
        for table in (
            "eval_runs",
            "eval_case_results",
            "eval_metrics",
            "eval_baselines",
            "verifier_findings",
            "llm_calls",
            "llm_call_attempts",
        ):
            for sql in (f"UPDATE {table} SET tenant_id = tenant_id", f"DELETE FROM {table}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql)
