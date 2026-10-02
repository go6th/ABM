"""Isolation-suite fixtures missing from the architecture listing.

Fixtures require a scratch PostgreSQL with pgvector and create/drop TEST_DB.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

HOST = os.getenv("PGHOST", "localhost")
PORT = os.getenv("PGPORT", "5432")
TEST_DB = os.getenv("TEST_DB", "ai_test")
ADMIN_USER = os.getenv("PGADMINUSER", "postgres")
APP_PASSWORD = os.getenv("APP_RW_PASSWORD", "local-dev-only")
ADMIN_MAINT_DSN = os.getenv("ADMIN_DSN", f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/postgres")
ADMIN_DSN = f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/{TEST_DB}"
APP_DSN = f"postgresql://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{TEST_DB}"

# Settings are read at import time; set them before any test module is imported.
os.environ.setdefault(
    "DATABASE_URL", f"postgresql+asyncpg://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{TEST_DB}"
)
os.environ.setdefault("CHECKPOINT_DATABASE_URL", APP_DSN)
os.environ.setdefault("DB_POOL_SIZE", "5")  # spike 1: a 5-connection pool
os.environ.setdefault("DB_MAX_OVERFLOW", "0")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


@pytest.fixture(scope="session", autouse=True)
def database(request: pytest.FixtureRequest):
    """Create the scratch database and run the same Alembic path as the migrator."""
    requires_postgres = any(
        item.get_closest_marker("requires_postgres") for item in request.session.items
    )
    if not requires_postgres:
        yield
        return
    with psycopg.connect(ADMIN_MAINT_DSN, autocommit=True) as c:
        c.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
        )
        # Roles are cluster-global and survive across scratch databases; migration 0002
        # creates them only when missing (a DROP here would fail once the dev database
        # has granted privileges to them).
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    env = os.environ | {
        "MIGRATOR_DATABASE_URL": os.getenv(
            "MIGRATOR_DATABASE_URL", f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/{TEST_DB}"
        ),
        "CHECKPOINT_DATABASE_URL": os.getenv(
            "MIGRATOR_DATABASE_URL", f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/{TEST_DB}"
        ),
        "APP_RW_PASSWORD": APP_PASSWORD,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=MIGRATIONS.parent,
        env=env,
    )
    yield


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.nodeid.startswith(("tests/isolation/", "tests/integration/", "tests/db/")):
            item.add_marker(pytest.mark.requires_postgres)


def _admin():
    return psycopg.connect(ADMIN_DSN, autocommit=True)


def _make_tenant(name: str) -> UUID:
    with _admin() as c:
        tid = c.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (name,)).fetchone()[
            0
        ]
        c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tid,))
    return tid


def _make_account(tenant: UUID, domain: str) -> UUID:
    with _admin() as c:
        return c.execute(
            "INSERT INTO accounts (tenant_id, domain) VALUES (%s, %s) RETURNING id",
            (tenant, domain),
        ).fetchone()[0]


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
            "INSERT INTO account_runs "
            "(tenant_id, account_id, graph_version, budget_json, reserved_credits, "
            "idempotency_key) "
            "VALUES (%s, %s, 'test', '{}', 1, %s) RETURNING id",
            (tenant_b, account_of_b, "k-" + uuid4().hex[:10]),
        ).fetchone()[0]


@pytest.fixture
def private_fact_of_a(tenant_a: UUID) -> UUID:
    acct = _make_account(tenant_a, f"a-{uuid4().hex[:6]}.example.com")
    with _admin() as c:
        c.execute(
            "INSERT INTO facts "
            "(tenant_id, account_id, attribute_key, value_json, fingerprint, visibility) "
            "VALUES (%s, %s, 'internal.note', '{}', 'fp-public', 'public')",
            (tenant_a, acct),
        )
        return c.execute(
            "INSERT INTO facts "
            "(tenant_id, account_id, attribute_key, value_json, fingerprint, visibility) "
            "VALUES (%s, %s, 'internal.note', '{\"secret\": true}', 'fp-private', 'private') "
            "RETURNING id",
            (tenant_a, acct),
        ).fetchone()[0]


@pytest.fixture
def tenant_a_dsn(tenant_a: UUID) -> str:
    """app_rw connection string whose session already carries tenant A's context."""
    return f"{APP_DSN}?options=-c%20app.tenant_id%3D{tenant_a}"


# Required by Settings (JWT verification)
os.environ.setdefault("JWKS_URL", "http://localhost:9/jwks.json")
os.environ.setdefault("JWT_AUDIENCE", "ai-saas")
os.environ.setdefault("JWT_ISSUER", "https://idp.example.com/")
