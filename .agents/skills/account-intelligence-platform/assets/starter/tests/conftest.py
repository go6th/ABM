"""tests/conftest.py - fixtures for the isolation suite. NOT in the Architecture document (it says the fixtures
create tenants A and B 'as the migrator' but does not show them). Needs a scratch PostgreSQL with pgvector.

Environment (defaults suit the compose.yml database on localhost):
  ADMIN_DSN     superuser or migrator DSN to the maintenance database (creates and drops the test database)
  TEST_DB       test database name (default ai_test)
  PGHOST/PGPORT host and port of the server (default localhost:5432)
"""
from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

HOST = os.getenv("PGHOST", "localhost")
PORT = os.getenv("PGPORT", "5432")
TEST_DB = os.getenv("TEST_DB", "ai_test")
ADMIN_USER = os.getenv("PGADMINUSER", "postgres")
APP_PASSWORD = "dev"
ADMIN_MAINT_DSN = os.getenv("ADMIN_DSN", f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/postgres")
ADMIN_DSN = f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/{TEST_DB}"
APP_DSN = f"postgresql://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{TEST_DB}"

# src.db.session reads these at import time, so set them before any test module is imported.
os.environ.setdefault("DATABASE_URL", f"postgresql+asyncpg://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{TEST_DB}")
os.environ.setdefault("CHECKPOINT_DATABASE_URL", APP_DSN)
os.environ.setdefault("DB_POOL_SIZE", "5")          # spike 1: a 5-connection pool
os.environ.setdefault("DB_MAX_OVERFLOW", "0")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture(scope="session", autouse=True)
def database():
    """Create the database, apply 0001, the checkpointer setup(), then 0002, exactly as the migrator job does."""
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(ADMIN_MAINT_DSN, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))
        c.execute("DROP ROLE IF EXISTS app_rw")
        c.execute("DROP ROLE IF EXISTS public_reader")
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute((MIGRATIONS / "0001_core.sql").read_text())
    with psycopg.connect(ADMIN_DSN, autocommit=True, prepare_threshold=0) as c:
        PostgresSaver(c).setup()                         # library-owned tables, created by the migrator, never at runtime
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute((MIGRATIONS / "0002_rls.sql").read_text())
        c.execute(f"ALTER ROLE app_rw PASSWORD '{APP_PASSWORD}'")   # the ARCH listing creates the role without one
    yield


def _admin():
    return psycopg.connect(ADMIN_DSN, autocommit=True)


def _make_tenant(name: str) -> UUID:
    with _admin() as c:
        tid = c.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tid,))
    return tid


def _make_account(tenant: UUID, domain: str) -> UUID:
    with _admin() as c:
        return c.execute("INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id", (tenant, domain)).fetchone()[0]


@pytest.fixture
def tenant_a() -> UUID:
    return _make_tenant("tenant-a-" + uuid4().hex[:6])


@pytest.fixture
def tenant_b() -> UUID:
    return _make_tenant("tenant-b-" + uuid4().hex[:6])


@pytest.fixture
def account_of_b(tenant_b: UUID) -> UUID:
    return _make_account(tenant_b, f"b-{uuid4().hex[:6]}.example.com")


@pytest.fixture
def run_of_b(tenant_b: UUID, account_of_b: UUID) -> UUID:
    with _admin() as c:
        return c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json, reserved_credits, idempotency_key) "
            "VALUES (%s, %s, 'test', '{}', 1, %s) RETURNING id",
            (tenant_b, account_of_b, "k-" + uuid4().hex[:10]),
        ).fetchone()[0]


@pytest.fixture
def private_fact_of_a(tenant_a: UUID) -> UUID:
    acct = _make_account(tenant_a, f"a-{uuid4().hex[:6]}.example.com")
    with _admin() as c:
        c.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint, visibility) "
            "VALUES (%s, %s, 'internal.note', '{}', 'fp-public', 'public')", (tenant_a, acct))
        return c.execute(
            "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json, fingerprint, visibility) "
            "VALUES (%s, %s, 'internal.note', '{\"secret\": true}', 'fp-private', 'private') RETURNING id", (tenant_a, acct)
        ).fetchone()[0]


@pytest.fixture
def tenant_a_dsn(tenant_a: UUID) -> str:
    """app_rw connection string whose session already carries tenant A's context."""
    return f"{APP_DSN}?options=-c%20app.tenant_id%3D{tenant_a}"

# src.api.auth reads these at import time
os.environ.setdefault("JWKS_URL", "http://localhost:9/jwks.json")
os.environ.setdefault("JWT_AUDIENCE", "ai-saas")
os.environ.setdefault("JWT_ISSUER", "https://idp.example.com/")
