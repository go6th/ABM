"""Hard-gate isolation for the grading tables (migration 0009): no cross-tenant read or write."""

from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from tests.conftest import ADMIN_DSN, _make_account

pytestmark = pytest.mark.asyncio


def _seed(tenant: UUID) -> dict[str, object]:
    acct = _make_account(tenant, f"g-{uuid4().hex[:6]}.example.com")
    run = uuid4()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c, c.transaction():
        fact = c.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint,"
            " product_grade, run_id)"
            " VALUES (%s, %s, 'jobs.a.b', '{}', 'fp', 'hypothesis', %s) RETURNING id",
            (tenant, acct, run),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind, supporting_sentence,"
            " call_ref)"
            " VALUES (%s, %s, 'search_proof', 'proof', 'c')",
            (tenant, fact),
        )
        c.execute(
            "INSERT INTO grade_events (tenant_id, fact_id, to_product, actor, reason_code)"
            " VALUES (%s, %s, 'hypothesis', 'test', 'initial')",
            (tenant, fact),
        )
        c.execute(
            "INSERT INTO claim_drops (tenant_id, run_id, reason_code, claim_hash, excerpt)"
            " VALUES (%s, %s, 'url_not_in_run', 'h', 'x')",
            (tenant, run),
        )
        lead = c.execute(
            "INSERT INTO lead_verifications (tenant_id, run_id, lead_key, status, accepted_count,"
            " categories)"
            " VALUES (%s, %s, 'k', 'pending', 0, '{}') RETURNING id",
            (tenant, run),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO lead_verification_queries (tenant_id, lead_verification_id, seq,"
            " query_text, tool_category,"
            " call_ref, accepted) VALUES (%s, %s, 1, 'q', 'web_search', 'c', true)",
            (tenant, lead),
        )
    return {"fact": fact, "run": run, "lead": lead}


TABLES = {
    "fact_evidence": "fact_id = :fact",
    "grade_events": "fact_id = :fact",
    "claim_drops": "run_id = :run",
    "lead_verifications": "run_id = :run",
    "lead_verification_queries": "lead_verification_id = :lead",
    "facts": "run_id = :run",
}


async def test_grading_tables_are_invisible_to_other_tenants(
    tenant_a: UUID, tenant_b: UUID
) -> None:
    ids = _seed(tenant_b)
    for table, where in TABLES.items():
        async with tenant_txn(tenant_a) as s:
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), ids)
            ).scalar_one()
        assert n == 0, table
        async with tenant_txn(tenant_b) as s:
            n = (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), ids)
            ).scalar_one()
        assert n == 1, table


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO claim_drops (tenant_id, run_id, reason_code, claim_hash, excerpt) VALUES"
        " (:t, :r, 'x', 'h9', 'x')",
        "INSERT INTO lead_verifications (tenant_id, run_id, lead_key, status, accepted_count,"
        " categories)"
        " VALUES (:t, :r, 'k', 'pending', 0, '{}')",
    ],
)
async def test_cannot_write_grading_rows_into_another_tenant(
    tenant_a: UUID, tenant_b: UUID, sql: str
) -> None:
    with pytest.raises(Exception, match="row-level security"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(text(sql), {"t": tenant_b, "r": uuid4()})


async def test_evidence_cannot_point_at_another_tenants_fact(
    tenant_a: UUID, tenant_b: UUID
) -> None:
    ids = _seed(tenant_b)
    with pytest.raises(Exception, match="foreign key|violates"):
        async with tenant_txn(tenant_a) as s:
            await s.execute(
                text(
                    "INSERT INTO fact_evidence (fact_id, evidence_kind, supporting_sentence,"
                    " call_ref)"
                    " VALUES (:f, 'search_proof', 'p', 'c')"
                ),
                {"f": ids["fact"]},
            )
