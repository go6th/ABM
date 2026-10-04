"""Shared spend test helpers (synthetic)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from spend.config import SpendConfig, load_spend_config

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def cfg() -> SpendConfig:
    return load_spend_config()


def capped_run(tenant: UUID, *, budget: str = "5.00", attended: bool = False, cap_required: bool = True) -> tuple[UUID, UUID, str]:
    """An account and a run that requires a cap (the default for new rows); budget_usd holds the
    created budget. Returns (account, run, domain)."""
    from tests.conftest import ADMIN_DSN

    domain = f"synthetic-{uuid4().hex[:8]}.example.com"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        account = c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id", (tenant, domain)
        ).fetchone()[0]
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd, tool_call_cap, attended, cap_required)"
            " VALUES (%s, %s, 'RUNNING', 'test', '{}', 1, %s, %s, 60, %s, %s) RETURNING id",
            (tenant, account, "k-" + uuid4().hex, budget, attended, cap_required),
        ).fetchone()[0]
    return account, run, domain


def admin(sql: str, *params: object) -> list[tuple[object, ...]]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []
