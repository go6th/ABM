"""The database refuses an invalid completion (tracker completion guard, AUDIT F-3).

GUARANTEE: a run cannot become completed while its tracker is not seeded, has an unset row, or has a
swept row; checked by the database for the runtime role on both run tables.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.requires_postgres


def _tenant_account(c: psycopg.Connection) -> tuple[UUID, UUID]:
    tenant = c.execute("INSERT INTO tenants (name) VALUES ('guard') RETURNING id").fetchone()[0]
    account = c.execute(
        "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
        " VALUES (%s, 'test-owner', %s) RETURNING id",
        (tenant, f"g{uuid4().hex[:8]}.example.com"),
    ).fetchone()[0]
    return tenant, account


def _running_run(c: psycopg.Connection, tenant: UUID, account: UUID) -> UUID:
    return c.execute(
        "INSERT INTO runs (tenant_id, account_id, run_type, status, config_version, credit_cap,"
        " started_at)"
        " VALUES (%s, %s, 'full', 'running', 'v', 10, now()) RETURNING id",
        (tenant, account),
    ).fetchone()[0]


def _seed(
    c: psycopg.Connection, tenant: UUID, run: UUID, statuses: list[str | None], swept: bool = False
) -> None:
    c.execute(
        "INSERT INTO run_coverage_meta (run_id, tenant_id, checklist_version, checklist_hash,"
        " seeded_row_count, pinned_policy) VALUES (%s, %s, 'v', 'h', %s, '{}')",
        (run, tenant, len(statuses)),
    )
    for i, st in enumerate(statuses):
        c.execute(
            "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, status,"
            " reason_code, proof, swept)"
            " VALUES (%s, %s, %s, 'high', %s, %s, %s, %s)",
            (
                run,
                f"P1.0{i}",
                tenant,
                st,
                "x" if st in ("not_done", "blocked") else None,
                "{}" if st else None,
                swept and st == "not_done",
            ),
        )


@pytest.mark.parametrize(
    ("statuses", "swept", "allowed"),
    [
        (None, False, False),  # tracker never seeded
        ([None, "done"], False, False),  # an unset row
        (["not_done", "done"], True, False),  # a swept row
        (["done", "none"], False, True),
    ],
)
def test_runs_completion_is_guarded(
    statuses: list[str | None] | None, swept: bool, allowed: bool
) -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        tenant, account = _tenant_account(c)
        run = _running_run(c, tenant, account)
        if statuses is not None:
            _seed(c, tenant, run, statuses, swept)
        try:
            c.execute(
                "UPDATE runs SET status = 'completed', finished_at = now() WHERE id = %s", (run,)
            )
            completed = True
        except psycopg.errors.CheckViolation:
            completed = False
        c.rollback()
    assert completed is allowed


def test_account_runs_completion_is_guarded_too() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        tenant, account = _tenant_account(c)
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
            " reserved_credits, idempotency_key) VALUES (%s, %s, 'RUNNING', 'g', '{}', 1, %s)"
            " RETURNING id",
            (tenant, account, uuid4().hex),
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation, match="no seeded tracker"):
            c.execute("UPDATE account_runs SET status = 'COMPLETED' WHERE id = %s", (run,))
        c.rollback()
