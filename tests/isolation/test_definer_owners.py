"""Cross-tenant definer functions run as NOLOGIN, non-superuser owners and still see every
tenant (security audit M1, migration 0070).

The scratch database's migrator is a superuser, so a function it owns sees every row whatever
the policies say; on Cloud SQL the migration user is not a superuser and such a function would
return nothing under FORCE RLS. Every cross-tenant definer function must therefore belong to a
role that cannot log in, is not a superuser and does not bypass RLS, and each sweeper lookup must
still return the rows of two synthetic tenants when called as the runtime role (``app_rw``, no
tenant context): the rows come from the owner's own policies and column grants, nothing else.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from tests.conftest import ADMIN_DSN, APP_DSN
from tests.isolation.matrix.plant import plant, tenant_tables

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gen_isolation_matrix as gen  # noqa: E402

pytestmark = pytest.mark.requires_postgres
MATRIX = gen.load_matrix()

OLD = "2020-01-01T00:00:00+00:00"
SWEEPER_LOOKUPS = (
    "runs_orphaned",
    "runs_stalled",
    "research_files_orphaned",
    "documents_orphaned",
    "document_parts_orphaned",
    "due_scheduled_refreshes",
    "tenants_with_waiting_jobs",
    "library_publication_counts",
    "maildrive_connections_due",
    "spend_runs_to_check",
    "tenants_with_credit_activity",
)


def test_every_definer_function_is_owned_by_a_nologin_role_without_superuser_or_bypassrls() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT p.proname, r.rolname, r.rolsuper, r.rolbypassrls, r.rolcanlogin"
            " FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' AND p.prosecdef"
        ).fetchall()
    assert rows
    bad = [r for r in rows if r[2] or r[3] or r[4]]
    assert not bad, bad
    owners = {name: role for name, role, *_ in rows}
    assert {owners[f] for f in SWEEPER_LOOKUPS} == {"sweeper_definer"}
    assert owners["write_platform_tombstone"] == "tombstone_writer"


class Planted:
    """Two synthetic tenants, each with rows every sweeper lookup should report."""

    def __init__(self) -> None:
        self.tenants: list[UUID] = []
        self.rows: dict[tuple[str, UUID], dict[str, Any]] = {}


@pytest.fixture(scope="module")
def planted() -> Iterator[Planted]:
    w = Planted()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tables = {t.name: t for t in tenant_tables(admin)}
        for n in range(2):
            tenant = admin.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"m1-{n}-synthetic",)
            ).fetchone()[0]
            w.tenants.append(tenant)

            def put(name: str, t: UUID = tenant, **over: Any) -> dict[str, Any]:
                base = (MATRIX["tables"].get(name) or {}).get("plant") or {}
                row = plant(admin, tables[name], t, {**base, **over})
                w.rows[(name, t)] = row
                return row

            account = put(
                "accounts",
                domain=f"m1-{n}-due.example",
                refresh_cadence="weekly",
                next_refresh_at=OLD,
            )
            # a run that is running, with no progress and no live lease since long ago
            put(
                "account_runs",
                account_id=account["id"],
                status="RUNNING",
                created_at=OLD,
                progress_at=None,
                stalled_at=None,
            )
            put("research_files", status="PARSING", updated_at=OLD, lease_expires_at=None)
            document = put(
                "documents",
                status="parsing",
                updated_at=OLD,
                lease_expires_at=None,
                object_key="{canary}-key",
                sha256="{hex64}",
            )
            put(
                "document_parts",
                document_id=document["id"],
                part_no=0,
                status="pending",
                updated_at=OLD,
            )
            # a job of another account waits for credit (the due account has none open)
            other = put("accounts", domain=f"m1-{n}-other.example")
            job = put(
                "research_jobs",
                account_id=other["id"],
                status="waiting_credit_limit",
                created_at=OLD,
                finished_at=None,
            )
            put("credit_entries", job_id=job["id"], at=OLD)
            put("library_publications", revoked_at=None)
            put(
                "connector_connections",
                kind="gmail",
                status="active",
                owner_user_id="m1-owner",
                last_synced_at=None,
                broken_reason=None,
            )
    yield w


def _as_runtime(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    """Call as app_rw with no tenant, no user and no system scope: only the owner's rights."""
    with psycopg.connect(APP_DSN) as c, c.transaction():
        return list(c.execute(sql, params).fetchall())


NOW = "2030-01-01T00:00:00+00:00"
CALLS: dict[str, tuple[str, tuple[Any, ...]]] = {
    "runs_orphaned": ("SELECT tenant_id FROM runs_orphaned(%s)", (NOW,)),
    "runs_stalled": ("SELECT tenant_id FROM runs_stalled(%s)", (NOW,)),
    "research_files_orphaned": ("SELECT tenant_id FROM research_files_orphaned(%s)", (NOW,)),
    "documents_orphaned": ("SELECT tenant_id FROM documents_orphaned(%s)", (NOW,)),
    "document_parts_orphaned": ("SELECT tenant_id FROM document_parts_orphaned(%s)", (NOW,)),
    "due_scheduled_refreshes": ("SELECT tenant_id FROM due_scheduled_refreshes(%s)", (NOW,)),
    "tenants_with_waiting_jobs": ("SELECT tenant_id FROM tenants_with_waiting_jobs()", ()),
    "maildrive_connections_due": ("SELECT tenant_id FROM maildrive_connections_due(%s)", (NOW,)),
    "spend_runs_to_check": ("SELECT tenant_id FROM spend_runs_to_check(%s)", (OLD,)),
    "tenants_with_credit_activity": (
        "SELECT tenant_id FROM tenants_with_credit_activity(%s)",
        (OLD,),
    ),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_each_sweeper_lookup_returns_both_tenants_rows_as_its_nologin_owner(
    planted: Planted, name: str
) -> None:
    sql, params = CALLS[name]
    seen = {row[0] for row in _as_runtime(sql, *params)}
    assert set(planted.tenants) <= seen, name


def test_library_counts_see_every_clients_publication(planted: Planted) -> None:
    rows = dict(_as_runtime("SELECT library_account_id, clients FROM library_publication_counts()"))
    for t in planted.tenants:
        assert rows.get(planted.rows[("library_publications", t)]["library_account_id"]) == 1


def test_the_tombstone_writer_inserts_once_and_reads_no_tenant_table() -> None:
    first = _as_runtime(
        "SELECT write_platform_tombstone('m1', gen_random_uuid()::text, 'test', NULL, now())"
    )
    assert first == [(True,)]
    with psycopg.connect(ADMIN_DSN) as c:
        readable = c.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relkind = 'r'"
            " AND has_table_privilege('tombstone_writer', c.oid, 'SELECT')"
        ).fetchone()[0]
    assert readable == 0


def test_the_sweeper_owner_reads_only_the_columns_its_lookups_use() -> None:
    """Column grants, never a table grant: no content column of any table is readable."""
    with psycopg.connect(ADMIN_DSN) as c:
        whole = c.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
            " AND has_table_privilege('sweeper_definer', c.oid, 'SELECT,INSERT,UPDATE,DELETE')"
        ).fetchall()
        content = c.execute(
            "SELECT has_column_privilege('sweeper_definer', 'documents', 'name', 'SELECT'),"
            " has_column_privilege('sweeper_definer', 'accounts', 'domain', 'SELECT'),"
            " has_column_privilege('sweeper_definer', 'account_runs', 'run_input', 'SELECT')"
        ).fetchone()
    assert whole == []
    assert content == (False, False, False)
