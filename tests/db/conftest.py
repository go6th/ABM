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

from src.db.session import make_sync_url


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
