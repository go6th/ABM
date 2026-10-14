"""Backup and restore: a logical backup of the application database restores to an equivalent,
still-isolated database.

The backup is ``pg_dump --format=custom`` of a freshly migrated database of its own (not the
shared scratch database, where the isolation matrix plants canary rows with foreign keys off)
after seeding two synthetic tenants; it is restored with ``pg_restore`` into a fresh database
(owners and grants kept: the roles are cluster-global). On the restored copy:

* every table in ``public`` has the same row count as the source;
* every table keeps its RLS flags (enabled and forced) and every policy is present unchanged;
* the Alembic revision matches the source (migrations can continue from the restore);
* tenant isolation still holds for the runtime role ``app_rw``: with the tenant set it sees only
  that tenant's accounts; with no tenant set it sees none.

The PostgreSQL client tools come from the host when installed, otherwise from the compose ``db``
container. This proves the logical backup path; Cloud SQL point-in-time recovery is a platform
feature proven only by a drill on the real instance (docs/runbooks/restore.md).

GUARANTEE(test_backup_restores_an_equivalent_isolated_database): backup/restore.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from tests.conftest import ADMIN_DSN, ADMIN_MAINT_DSN, ADMIN_USER, APP_PASSWORD, HOST, PORT, TEST_DB

pytestmark = [pytest.mark.requires_postgres]
ROOT = Path(__file__).resolve().parents[2]
SOURCE = f"{TEST_DB}_backup_src"
RESTORED = f"{TEST_DB}_restored"
SOURCE_ADMIN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{SOURCE}"


def _tool(name: str) -> list[str] | None:
    if shutil.which(name):
        return [name, "-h", HOST, "-p", PORT, "-U", ADMIN_USER]
    if shutil.which("docker") is None:
        return None
    # CI passes its Postgres service container id; locally the compose ``db`` service is used
    cid = (
        os.getenv("PG_CONTAINER")
        or subprocess.run(
            ["docker", "compose", "ps", "-q", "db"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
    )
    if not cid:
        return None
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        "PGPASSWORD=" + os.getenv("PGPASSWORD", "dev"),
        cid,
        name,
        "-U",
        ADMIN_USER,
    ]


def _snapshot(dsn: str) -> dict[str, object]:
    with psycopg.connect(dsn) as c:
        tables = [
            r[0]
            for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
            )
        ]
        counts = {
            t: c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in tables
        }
        rls = c.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class c JOIN"
            " pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
            " AND c.relkind = 'r' ORDER BY 1"
        ).fetchall()
        policies = c.execute(
            "SELECT tablename, policyname, cmd, roles::text, qual, with_check FROM pg_policies"
            " WHERE schemaname = 'public' ORDER BY 1, 2"
        ).fetchall()
        revision = c.execute("SELECT version_num FROM alembic_version").fetchall()
    return {"counts": counts, "rls": rls, "policies": policies, "revision": revision}


def _recreate(name: str) -> None:
    with psycopg.connect(ADMIN_MAINT_DSN, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _migrated_source() -> None:
    """A fresh database migrated by the same Alembic path as the session fixture."""
    _recreate(SOURCE)
    env = os.environ | {
        "MIGRATOR_DATABASE_URL": SOURCE_ADMIN,
        "CHECKPOINT_DATABASE_URL": SOURCE_ADMIN,
        "APP_RW_PASSWORD": APP_PASSWORD,
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True, cwd=ROOT, env=env
    )


def _seed() -> tuple[uuid.UUID, uuid.UUID]:
    with psycopg.connect(SOURCE_ADMIN, autocommit=True) as c:
        ids = []
        for name in ("restore-a", "restore-b"):
            t = c.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-synthetic",)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
                " VALUES (%s, 'test-owner', %s)",
                (t, f"{name}.example"),
            )
            ids.append(t)
    return ids[0], ids[1]


def test_backup_restores_an_equivalent_isolated_database(tmp_path: Path) -> None:
    dump, restore = _tool("pg_dump"), _tool("pg_restore")
    if dump is None or restore is None:
        pytest.skip("no pg_dump/pg_restore on the host and no compose db container")
    _migrated_source()
    tenant_a, tenant_b = _seed()
    source = _snapshot(SOURCE_ADMIN)

    backup = subprocess.run(
        [*dump, "--format=custom", "--dbname", SOURCE], capture_output=True, check=True
    ).stdout
    assert len(backup) > 10_000
    (tmp_path / "backup.dump").write_bytes(backup)

    _recreate(RESTORED)
    try:
        restored = subprocess.run(
            [*restore, "--exit-on-error", "--dbname", RESTORED],
            input=backup,
            capture_output=True,
            check=False,
        )
        if restored.returncode:
            detail = restored.stderr.decode("utf-8", errors="replace")[-4000:]
            raise AssertionError(f"pg_restore failed with exit {restored.returncode}: {detail}")
        restored_admin = ADMIN_DSN.rsplit("/", 1)[0] + f"/{RESTORED}"
        assert _snapshot(restored_admin) == source

        app = f"postgresql://app_rw:{APP_PASSWORD}@{HOST}:{PORT}/{RESTORED}"
        with psycopg.connect(app) as c:
            assert c.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0  # no tenant set
            with c.transaction():
                c.execute(
                    "SELECT set_config('app.tenant_id', %s, true),"
                    " set_config('app.scope', 'system', true)",
                    (str(tenant_a),),
                )
                seen = {r[0] for r in c.execute("SELECT tenant_id FROM accounts")}
            assert seen == {tenant_a}
            assert tenant_b not in seen
    finally:
        with psycopg.connect(ADMIN_MAINT_DSN, autocommit=True) as c:
            for name in (RESTORED, SOURCE):
                c.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
