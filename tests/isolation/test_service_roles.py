"""Migration 0101 (ADR-0123): the runtime role is split by service.

* ``app_api``: app_rw's rights MINUS every write on ``tool_calls``, every right on ``run_leases``
  and the LangGraph checkpoint tables (``checkpoints`` stays readable: the run steps panel and
  the live page read the finished nodes), and EXECUTE on the sweeper's tenant-less discovery
  lookups; its own copy of every app_rw policy.
* ``app_worker``: a member of app_rw (the research slices, the sweeper, the jobs).
* ``app_sweeper``: EXECUTE on the sweeper's discovery lookups and nothing else.

All three are NOLOGIN and NOBYPASSRLS; the Cloud SQL IAM users join them. Each proves that a
query with no tenant context returns zero rows (or is refused outright), and app_api cannot
write the ledger of provider calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql

from tests.conftest import ADMIN_DSN

pytestmark = pytest.mark.requires_postgres
ROLES = ("app_api", "app_worker", "app_sweeper")
API_NO_ACCESS = ("run_leases", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")
API_READ_ONLY = ("tool_calls", "checkpoints")
DISCOVERY = (
    "runs_orphaned",
    "runs_stalled",
    "documents_orphaned",
    "document_parts_orphaned",
    "research_files_orphaned",
)


@contextmanager
def _as(role: str, tenant: UUID | None = None) -> Iterator[psycopg.Connection]:
    """One transaction as ``role`` (the superuser test login switches with SET LOCAL ROLE, so
    the role's own grants and policies decide), with or without a tenant context."""
    with psycopg.connect(ADMIN_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            ("" if tenant is None else str(tenant),),
        )
        c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        yield c


def _count(c: psycopg.Connection, query: str) -> int:
    row = c.execute(query).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return int(row[0])


def test_the_service_roles_cannot_log_in_superuse_or_bypass_rls() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT rolname, rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole"
            " FROM pg_roles WHERE rolname = ANY(%s) ORDER BY 1",
            (list(ROLES),),
        ).fetchall()
    assert rows == [(r, False) for r in sorted(ROLES)]


@pytest.mark.parametrize("role", ["app_api", "app_worker"])
def test_a_tenantless_query_returns_zero_rows(role: str, account_of_b: UUID) -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        tenant_b = c.execute("SELECT tenant_id FROM accounts WHERE id = %s", (account_of_b,))
        tid = UUID(str(tenant_b.fetchone()[0]))  # type: ignore[index]
    with _as(role) as c:
        assert _count(c, "SELECT count(*) FROM accounts") == 0
        assert _count(c, "SELECT count(*) FROM account_runs") == 0
        assert _count(c, "SELECT count(*) FROM tool_calls") == 0
    # the zero above is the policy, not a missing one: with the tenant set the row is there
    with _as(role, tid) as c:
        got = c.execute("SELECT id FROM accounts WHERE id = %s", (account_of_b,)).fetchall()
        assert [UUID(str(r[0])) for r in got] == [account_of_b]
    with _as(role, uuid4()) as c:
        assert _count(c, "SELECT count(*) FROM accounts") == 0


def test_the_sweeper_role_reads_no_table_and_calls_only_the_discovery_lookups() -> None:
    with _as("app_sweeper") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT count(*) FROM accounts")
    with _as("app_sweeper") as c:
        for fn in DISCOVERY:
            c.execute(
                sql.SQL("SELECT count(*) FROM {}(now())").format(sql.Identifier(fn))
            ).fetchone()
        c.execute("SELECT count(*) FROM operator_tenant_overview()").fetchone()
    with psycopg.connect(ADMIN_DSN) as c:
        tables = _count(
            c,
            "SELECT count(*) FROM pg_class c, aclexplode(c.relacl) a WHERE a.grantee ="
            " 'app_sweeper'::regrole AND c.relnamespace = 'public'::regnamespace",
        )
        fns = {
            r[0]
            for r in c.execute(
                "SELECT p.proname FROM pg_proc p, aclexplode(p.proacl) a WHERE a.grantee ="
                " 'app_sweeper'::regrole AND p.pronamespace = 'public'::regnamespace"
            ).fetchall()
        }
    assert tables == 0
    assert fns == {*DISCOVERY, "operator_tenant_overview"}


def test_app_api_cannot_insert_into_tool_calls() -> None:
    with _as("app_api", uuid4()) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("INSERT INTO tool_calls DEFAULT VALUES")
    with _as("app_api", uuid4()) as c:  # reading the ledger (run counters) stays allowed
        assert _count(c, "SELECT count(*) FROM tool_calls") == 0
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute("SELECT has_table_privilege('app_worker', 'tool_calls', 'INSERT')")
        assert row.fetchone() == (True,)


@pytest.mark.parametrize("table", API_NO_ACCESS)
def test_app_api_has_no_access_to_leases_and_checkpoint_internals(table: str) -> None:
    with _as("app_api", uuid4()) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(table)))


def test_app_api_reads_checkpoints_but_never_writes_them_nor_runs_the_sweeper() -> None:
    with _as("app_api", uuid4()) as c:
        assert _count(c, "SELECT count(*) FROM checkpoints") == 0
    with _as("app_api", uuid4()) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("DELETE FROM checkpoints")
    with _as("app_api") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT count(*) FROM runs_orphaned(now())")


def _privileges(c: psycopg.Connection, role: str) -> set[tuple[str, str]]:
    tables = c.execute(
        "SELECT c.relname, a.privilege_type FROM pg_class c, aclexplode(c.relacl) a"
        " WHERE a.grantee = %s::regrole AND c.relnamespace = 'public'::regnamespace",
        (role,),
    ).fetchall()
    columns = c.execute(
        "SELECT c.relname || '.' || t.attname, a.privilege_type FROM pg_attribute t"
        " JOIN pg_class c ON c.oid = t.attrelid, aclexplode(t.attacl) a"
        " WHERE a.grantee = %s::regrole AND c.relnamespace = 'public'::regnamespace",
        (role,),
    ).fetchall()
    fns = c.execute(
        "SELECT p.oid::regprocedure::text, a.privilege_type FROM pg_proc p, aclexplode(p.proacl) a"
        " WHERE a.grantee = %s::regrole AND p.pronamespace = 'public'::regnamespace",
        (role,),
    ).fetchall()
    return {(str(n), str(p)) for n, p in [*tables, *columns, *fns]}


def _excluded(name: str, privilege: str) -> bool:
    obj = name.split("(", 1)[0].split(".", 1)[0]
    return (
        obj in API_NO_ACCESS or (obj in API_READ_ONLY and privilege != "SELECT") or obj in DISCOVERY
    )


def test_app_api_holds_exactly_app_rw_rights_minus_the_exclusions() -> None:
    """The sync runs after every migration (migrations/env.py), so a later GRANT to app_rw or a
    later app_rw policy reaches app_api without anyone remembering to copy it."""
    with psycopg.connect(ADMIN_DSN) as c:
        rw, api = _privileges(c, "app_rw"), _privileges(c, "app_api")
        assert api == {p for p in rw if not _excluded(*p)}
        policies = c.execute(
            "SELECT tablename, cmd, permissive, qual, with_check, roles::text[]"
            " FROM pg_policies WHERE schemaname = 'public'"
            " AND roles && ARRAY['app_rw', 'app_api']::name[]"
        ).fetchall()
    rw_rules = sorted(p[:5] for p in policies if "app_rw" in p[5])
    api_rules = sorted(p[:5] for p in policies if p[5] == ["app_api"])
    assert rw_rules and api_rules == rw_rules


def test_the_sync_copies_a_later_grant_and_policy() -> None:
    table = f"m101_probe_{uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            sql.SQL("CREATE TABLE {} (tenant_id uuid NOT NULL)").format(sql.Identifier(table))
        )
        try:
            c.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO app_rw").format(sql.Identifier(table)))
            c.execute(
                sql.SQL(
                    "CREATE POLICY probe ON {} TO app_rw USING (tenant_id = app_tenant())"
                    " WITH CHECK (tenant_id = app_tenant())"
                ).format(sql.Identifier(table))
            )
            c.execute("SELECT service_roles_sync()")
            c.execute("SELECT service_roles_sync()")  # idempotent
            granted = c.execute(
                "SELECT has_table_privilege('app_api', %s, 'INSERT')", (table,)
            ).fetchone()
            copies = c.execute(
                "SELECT count(*) FROM pg_policies WHERE tablename = %s AND roles = '{app_api}'",
                (table,),
            ).fetchone()
            assert granted == (True,) and copies == (1,)
        finally:
            c.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table)))


def _direct(c: psycopg.Connection, member: str) -> set[str]:
    return {
        str(r[0])
        for r in c.execute(
            "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid"
            " WHERE m.member = (SELECT u.oid FROM pg_roles u WHERE u.rolname = %s)",
            (member,),
        ).fetchall()
    }


def _sync(c: psycopg.Connection, api: str, worker: str) -> None:
    with c.transaction():
        c.execute(
            "SELECT set_config('app.api_iam_users', %s, true),"
            " set_config('app.worker_iam_users', %s, true)",
            (api, worker),
        )
        c.execute("SELECT service_roles_sync()")


def test_iam_users_move_from_app_rw_to_their_service_role() -> None:
    # named like Cloud SQL IAM users ("<service account>@<project>.iam"): the "@" and "." are
    # not plain identifier characters (staging 2026-11-08: a ``name::regrole`` cast refused them)
    tag = uuid4().hex[:8]
    api, worker = f"m101-api-{tag}@synthetic.iam", f"m101-worker-{tag}@synthetic.iam"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for role in (api, worker):
            c.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            c.execute(sql.SQL("GRANT app_rw TO {}").format(sql.Identifier(role)))  # as 0071 did
        try:
            _sync(c, "", "")  # unset: nothing moves
            assert _direct(c, api) == {"app_rw"}
            _sync(c, f" {api} ,m101_not_created_yet", worker)
            _sync(c, api, worker)  # idempotent
            assert _direct(c, api) == {"app_api"}
            assert _direct(c, worker) == {"app_worker"}
            # the worker keeps app_rw's rights through app_worker; the api does not
            row = c.execute(
                "SELECT pg_has_role(%s, 'app_rw', 'USAGE'), pg_has_role(%s, 'app_rw', 'USAGE')",
                (worker, api),
            ).fetchone()
            assert row == (True, False)
        finally:
            for role in (api, worker):
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def test_a_bypassrls_iam_user_stops_the_sync() -> None:
    role = f"m101_bad_{uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN BYPASSRLS").format(sql.Identifier(role)))
        try:
            with pytest.raises(psycopg.errors.RaiseException):
                _sync(c, role, "")
            assert _direct(c, role) == set()
        finally:
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
