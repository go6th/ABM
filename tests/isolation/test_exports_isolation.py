"""Hard isolation gate for the export tables (migration 0016): every table is invisible across
tenants, returns nothing without a tenant context, refuses a cross-tenant write (WITH CHECK), and
is insert-only for ``app_rw`` (no UPDATE or DELETE)."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN, APP_DSN

pytestmark = pytest.mark.requires_postgres
TABLES = ("report_sections", "deliverables")
H = "a" * 64
INSERTS = {
    "report_sections": (
        "INSERT INTO report_sections (tenant_id, account_id, run_id, version, kind, position,"
        " text) VALUES (%(t)s, %(a)s, %(r)s, 1, 'caveat', 0, 'Synthetic caveat.')"
    ),
    "deliverables": (
        "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
        " filename, sha256, size_bytes, object_key, manifest_json, content_hash, snapshot_hash,"
        " theme_hash, rules_hash, built_at) VALUES (%(t)s, %(a)s, %(r)s, %(h)s, 'summary',"
        " %(v)s, 'Synthetic.docx', %(h)s, 1, %(key)s, '{}', %(h)s, %(h)s, %(h)s, %(h)s, now())"
    ),
}


def _params(tenant: UUID, account: UUID) -> dict[str, object]:
    run = uuid4()
    return {
        "t": tenant,
        "a": account,
        "r": run,
        "h": H,
        "v": 1,
        "key": f"{tenant}/{account}/{run}/deliverables/{H}.docx",
    }


def _app(
    tenant: UUID | None, sql: str, params: dict[str, object] | None = None
) -> list[tuple[object, ...]]:
    with psycopg.connect(APP_DSN) as c, c.transaction():
        if tenant is not None:
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        cur = c.execute(sql, params or {})
        return cur.fetchall() if cur.description else []


@pytest.fixture
def seeded(tenant_b: UUID, account_of_b: UUID) -> UUID:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for sql in INSERTS.values():
            c.execute(sql, _params(tenant_b, account_of_b))
    return account_of_b


@pytest.mark.parametrize("table", TABLES)
def test_export_tables_invisible_across_tenants(table: str, tenant_a: UUID, seeded: UUID) -> None:
    assert _app(tenant_a, f"SELECT count(*) FROM {table}")[0][0] == 0


@pytest.mark.parametrize("table", TABLES)
def test_export_tables_fail_closed_without_tenant(table: str, seeded: UUID) -> None:
    assert _app(None, f"SELECT count(*) FROM {table}")[0][0] == 0


@pytest.mark.parametrize("table", TABLES)
def test_export_tables_refuse_cross_tenant_writes(
    table: str, tenant_a: UUID, tenant_b: UUID, account_of_b: UUID
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        _app(tenant_a, INSERTS[table], _params(tenant_b, account_of_b))


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("verb", ["UPDATE {t} SET created_at = now()", "DELETE FROM {t}"])
def test_export_tables_are_insert_only(table: str, verb: str, tenant_b: UUID, seeded: UUID) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _app(tenant_b, verb.format(t=table))
