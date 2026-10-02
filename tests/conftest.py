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
import yaml
from psycopg import sql

HOST = os.getenv("PGHOST", "localhost")
PORT = os.getenv("PGPORT", "5432")
TEST_DB = os.getenv("TEST_DB", "ai_test")
ADMIN_USER = os.getenv("PGADMINUSER", "postgres")
APP_PASSWORD = os.getenv("APP_RW_PASSWORD", "local-dev-only")
ADMIN_MAINT_DSN = os.getenv("ADMIN_DSN", f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/postgres")
ADMIN_DSN = f"postgresql://{ADMIN_USER}@{HOST}:{PORT}/{TEST_DB}"
APP_DSN = f"postgresql://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{TEST_DB}"

# src.db.session reads these at import time, so set them before any test module is imported.
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
        c.execute("DROP ROLE IF EXISTS app_rw")
        c.execute("DROP ROLE IF EXISTS public_reader")
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
        if item.nodeid.startswith(("tests/isolation/", "tests/integration/")):
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
        run_id = c.execute(
            "INSERT INTO account_runs "
            "(tenant_id, account_id, graph_version, budget_json, reserved_credits, "
            "idempotency_key) "
            "VALUES (%s, %s, 'test', '{}', 1, %s) RETURNING id",
            (tenant_b, account_of_b, "k-" + uuid4().hex[:10]),
        ).fetchone()[0]
        checklist = yaml.safe_load(
            (MIGRATIONS.parent / "config" / "checklist.yaml").read_text(encoding="utf-8")
        )
        c.execute(
            "UPDATE account_runs SET checklist_version = %s, expected_coverage_rows = %s "
            "WHERE tenant_id = %s AND id = %s",
            (checklist["version"], len(checklist["rows"]), tenant_b, run_id),
        )
        c.cursor().executemany(
            "INSERT INTO coverage_rows (tenant_id, account_id, run_id, checklist_version, "
            "row_key, pass, description, priority) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    tenant_b,
                    account_of_b,
                    run_id,
                    checklist["version"],
                    row["id"],
                    row["pass"],
                    row["row"],
                    row["priority"],
                )
                for row in checklist["rows"]
            ],
        )
        return run_id


@pytest.fixture
def private_fact_of_a(tenant_a: UUID) -> UUID:
    acct = _make_account(tenant_a, f"a-{uuid4().hex[:6]}.example.com")
    with psycopg.connect(ADMIN_DSN) as c:
        source_ids = [
            c.execute(
                "INSERT INTO sources (tenant_id, account_id, source_code, url, url_hash, domain, "
                "tier, lineage_group) VALUES (%s, %s, %s, %s, %s, %s, 'T1', %s) RETURNING id",
                (
                    tenant_a,
                    acct,
                    f"S-{number:04d}",
                    f"https://example.com/source-{number}",
                    f"{number:064x}",
                    "example.com",
                    f"fixture-{number}",
                ),
            ).fetchone()[0]
            for number in (1, 2)
        ]
        fact_ids = [
            c.execute(
                "INSERT INTO facts "
                "(tenant_id, account_id, attribute_key, value_json, fingerprint, visibility) "
                "VALUES (%s, %s, 'internal.note', %s::jsonb, %s, %s) RETURNING id",
                (tenant_a, acct, value, fingerprint, visibility),
            ).fetchone()[0]
            for value, fingerprint, visibility in (
                ("{}", "fp-public", "public"),
                ('{"secret": true}', "fp-private", "private"),
            )
        ]
        for fact_id, source_id, code, quote in (
            (fact_ids[0], source_ids[0], "S-0001", "Public evidence"),
            (fact_ids[1], source_ids[1], "S-0002", "Private evidence"),
        ):
            c.execute(
                "INSERT INTO fact_evidence (tenant_id, account_id, fact_id, source_id, "
                "source_code, lineage_group, quote_text) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tenant_a, acct, fact_id, source_id, code, f"fixture-{code}", quote),
            )
        return fact_ids[1]


@pytest.fixture
def tenant_a_dsn(tenant_a: UUID) -> str:
    """app_rw connection string whose session already carries tenant A's context."""
    return f"{APP_DSN}?options=-c%20app.tenant_id%3D{tenant_a}"


# src.api.auth reads these at import time
os.environ.setdefault("JWKS_URL", "http://localhost:9/jwks.json")
os.environ.setdefault("JWT_AUDIENCE", "ai-saas")
os.environ.setdefault("JWT_ISSUER", "https://idp.example.com/")
