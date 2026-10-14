"""Migration 0071: the IAM database users named in APP_RW_IAM_USERS become members of app_rw and
nothing else; unknown names are skipped, an empty list is a no-op, and a superuser or BYPASSRLS
role stops the migration (security audit L16)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.requires_postgres
SQL = (Path(__file__).resolve().parents[2] / "migrations/0071_app_rw_iam_users.sql").read_text()
DOWN = (
    Path(__file__).resolve().parents[2] / "migrations/0071_app_rw_iam_users.down.sql"
).read_text()


def _run(c: psycopg.Connection, text: str, names: str) -> None:
    with c.transaction():
        c.execute("SELECT set_config('app.rw_iam_users', %s, true)", (names,))
        c.execute(text)  # type: ignore[arg-type]


def _member(c: psycopg.Connection, role: str) -> bool:
    return bool(
        c.execute("SELECT pg_has_role(%s, 'app_rw', 'USAGE')", (role,)).fetchone()[0]  # type: ignore[index]
    )


def test_named_iam_users_join_app_rw_and_nothing_else() -> None:
    api, worker = f"m71_api_{uuid4().hex[:8]}", f"m71_worker_{uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for role in (api, worker):
            c.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
        try:
            _run(c, SQL, "")  # unset: nothing happens
            assert not _member(c, api)
            _run(c, SQL, f" {api} , {worker},m71_not_created_yet")
            _run(c, SQL, f"{api},{worker}")  # idempotent
            assert _member(c, api) and _member(c, worker)
            owned = c.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner"
                " WHERE r.rolname = ANY(%s)",
                ([api, worker],),
            ).fetchone()[0]  # type: ignore[index]
            flags = c.execute(
                "SELECT bool_or(rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname = ANY(%s)",
                ([api, worker],),
            ).fetchone()[0]  # type: ignore[index]
            assert owned == 0 and flags is False
            _run(c, DOWN, f"{api},{worker}")
            assert not _member(c, api) and not _member(c, worker)
        finally:
            for role in (api, worker):
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_a_bypassrls_role_stops_the_migration() -> None:
    role = f"m71_bad_{uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN BYPASSRLS").format(sql.Identifier(role)))
        try:
            with pytest.raises(psycopg.errors.RaiseException):
                _run(c, SQL, role)
            assert not _member(c, role)
        finally:
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
