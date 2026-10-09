"""Phase 4 fixtures: synthetic tenants and accounts, a refresh run row, and facts with evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg

from tests.conftest import ADMIN_DSN
from tests.phase3.conftest import browser, plane  # noqa: F401  (signed-in API fixtures)

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def db(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    """Admin query (bypasses RLS)."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def tenant_with_account(name: str, domain: str | None = None) -> tuple[UUID, UUID]:
    """A synthetic tenant with one account."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        t = c.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-synthetic",)
        ).fetchone()[0]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (t,))
        a = c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (t, domain or f"{uuid4().hex[:8]}.example"),
        ).fetchone()[0]
    return t, a


def fact(tenant: UUID, account: UUID, *, key: str = "news.event", grade: str = "probable") -> UUID:
    """A fact with its evidence row (no fact without a source)."""
    question = (
        "What evidence would confirm this synthetic hypothesis?" if grade == "hypothesis" else None
    )
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c, c.transaction():
        f = c.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint,"
            " confirming_question, product_grade, read_in_primary_this_run, may_state_to_prospect)"
            " VALUES (%s, %s, %s, '{}', %s, %s, %s, %s, %s) RETURNING id",
            (
                tenant,
                account,
                key,
                uuid4().hex,
                question,
                grade,
                grade == "confirmed",
                grade == "confirmed",
            ),
        ).fetchone()[0]
        c.execute(
            "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind,"
            " supporting_sentence, call_ref) VALUES (%s, %s, 'search_proof',"
            " 'synthetic evidence', 'fixture-call')",
            (tenant, f),
        )
    return f
