"""Fixtures for database tests: sync engine, tenant-scoped session, admin helpers.

The scratch database and migrations come from the root tests/conftest.py
(requires_postgres). These fixtures add sync-session access for data layer
tests. Writes through the sync engine run as app_rw, so RLS applies: use
tenant_session() for tenant data, admin for raw SQL that must bypass RLS.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from account_intelligence.db.session import make_sync_url


@pytest.fixture(scope="session")
def sync_engine() -> Iterator[Engine]:
    engine = create_engine(make_sync_url(os.environ["DATABASE_URL"]), pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    """Autocommit admin connection to the scratch database; bypasses RLS."""
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    user = os.getenv("PGADMINUSER", "postgres")
    dsn = f"postgresql://{user}@{host}:{port}/{os.getenv('TEST_DB', 'ai_test')}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def db(sync_engine: Engine) -> Iterator[Session]:
    """Session whose transaction is rolled back after the test (per-test isolation)."""
    with sync_engine.connect() as conn:
        txn = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield session
        session.close()
        txn.rollback()


def set_tenant(session: Session, tenant_id: UUID) -> None:
    """Bind the session's transaction to a tenant for RLS (transaction-local)."""
    session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def make_tenant(conn: psycopg.Connection, name: str | None = None) -> UUID:
    row = conn.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id",
        (name or ("tenant-" + uuid4().hex[:8]),),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def make_account(conn: psycopg.Connection, tenant: UUID, domain: str | None = None) -> UUID:
    row = conn.execute(
        "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
        (tenant, domain or f"{uuid4().hex[:10]}.example.com"),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def make_run(
    conn: psycopg.Connection,
    tenant: UUID,
    account: UUID,
    status: str = "queued",
    *,
    run_type: str = "full",
    parent: UUID | None = None,
    credit_cap: int | None = 100,
) -> UUID:
    """Insert a run whose timestamps and error are consistent with ``status``."""
    started = None if status in ("queued", "failed") else "now()"
    finished = "now()" if status in ("completed", "failed") else None
    row = conn.execute(
        "INSERT INTO runs (tenant_id, account_id, run_type, status, config_version, credit_cap,"
        " parent_run_id, started_at, finished_at, error)"
        f" VALUES (%s, %s, %s, %s, 'cfg-test', %s, %s, {started or 'NULL'}, {finished or 'NULL'},"
        " %s) RETURNING id",
        (
            tenant,
            account,
            run_type,
            status,
            credit_cap,
            parent,
            "synthetic failure" if status == "failed" else None,
        ),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def make_source(
    conn: psycopg.Connection,
    tenant: UUID,
    account: UUID,
    run: UUID,
    code: str = "S-0001",
    url: str | None = None,
    kind: str = "primary",
) -> UUID:
    """Insert a source; the hash is computed by the database from the normalized URL."""
    url = url or f"https://news.example.com/{uuid4().hex[:8]}"
    row = conn.execute(
        "INSERT INTO sources (tenant_id, account_id, source_code, url, url_normalized, url_hash,"
        " kind, first_seen_run_id, last_seen_run_id)"
        " VALUES (%s, %s, %s, %s, %s, encode(sha256(convert_to(%s, 'UTF8')), 'hex'), %s, %s, %s)"
        " RETURNING id",
        (tenant, account, code, url, url, url, kind, run, run),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def seed_complete_tracker(conn: psycopg.Connection, tenant: UUID, run: UUID) -> None:
    """Seed a one-row tracker whose row is done, so the run may complete (migration 0010 guard).

    Tests that exercise run lifecycle rules other than completion use this to satisfy the
    completion guard; the guard itself is tested in tests/db/test_run_completion_guard.py.
    """
    conn.execute(
        "INSERT INTO run_coverage_meta (run_id, tenant_id, checklist_version, checklist_hash,"
        " seeded_row_count, pinned_policy) VALUES (%s, %s, 'test', 'test', 1, '{}')",
        (run, tenant),
    )
    conn.execute(
        "INSERT INTO coverage_rows (run_id, row_key, tenant_id, priority, status, proof)"
        " VALUES (%s, 'P1.01', %s, 'high', 'done', '{}')",
        (run, tenant),
    )
