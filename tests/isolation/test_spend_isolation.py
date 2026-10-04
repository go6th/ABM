"""Hard isolation gate for the credit cap tables (migration 0015): every table is invisible across
tenants, returns nothing without a tenant context, refuses a cross-tenant write (WITH CHECK) and
is insert-only for ``app_rw``; the tenant role (``public_reader``) cannot read ``operator_json``."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN, APP_DSN

pytestmark = pytest.mark.requires_postgres
H = "a" * 64
TABLES = (
    "run_cap_versions",
    "run_cap_events",
    "balance_snapshots",
    "spend_reports",
    "prepulled_files",
    "spend_defects",
)
INSERTS = {
    "run_cap_versions": (
        "INSERT INTO run_cap_versions (tenant_id, run_id, version, cap_usd, proposal_json,"
        " proposal_hash, config_hash) VALUES (%(t)s, %(r)s, 1, 1, '{}', %(h)s, %(h)s)"
    ),
    "balance_snapshots": (
        "INSERT INTO balance_snapshots (tenant_id, run_id, phase, provider_code, status, source,"
        " taken_at) VALUES (%(t)s, %(r)s, 'start', 'p_search', 'unknown', 'synthetic', now())"
    ),
    "spend_reports": (
        "INSERT INTO spend_reports (tenant_id, run_id, version, status, content_hash,"
        " operator_json, tenant_json) VALUES (%(t)s, %(r)s, 1, 'reconciled', %(h)s,"
        " '{\"by_provider\": []}', '{}')"
    ),
    "prepulled_files": (
        "INSERT INTO prepulled_files (tenant_id, run_id, provider_code, operation, sha256,"
        " supplied_by, object_ref, record_count) VALUES (%(t)s, %(r)s, 'p_search', 'web_search',"
        " %(h)s, 'operator:u', 'ref', 0)"
    ),
    "spend_defects": (
        "INSERT INTO spend_defects (tenant_id, run_id, provider_code, operation, kind,"
        " charged_usd) VALUES (%(t)s, %(r)s, 'p_search', 'web_search', 'free_operation_charged', 1)"
    ),
}
# run_cap_events references a version of the same run: seeded after its version
EVENT = (
    "INSERT INTO run_cap_events (tenant_id, run_id, version, event) VALUES (%(t)s, %(r)s, 1,"
    " 'proposed')"
)


def _app(
    tenant: UUID | None, sql: str, params: dict[str, object] | None = None, role: str | None = None
) -> list[tuple[object, ...]]:
    with psycopg.connect(APP_DSN) as c, c.transaction():
        if tenant is not None:
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        if role is not None:
            c.execute(f"SET LOCAL ROLE {role}")
        cur = c.execute(sql, params or {})
        return cur.fetchall() if cur.description else []


@pytest.fixture
def seeded(tenant_b: UUID) -> UUID:
    params = {"t": tenant_b, "r": uuid4(), "h": H}
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for sql in INSERTS.values():
            c.execute(sql, params)
        c.execute(EVENT, params)
    return tenant_b


@pytest.mark.parametrize("table", TABLES)
def test_spend_tables_invisible_across_tenants(table: str, tenant_a: UUID, seeded: UUID) -> None:
    assert _app(tenant_a, f"SELECT count(*) FROM {table}")[0][0] == 0
    assert _app(seeded, f"SELECT count(*) FROM {table}")[0][0] >= 1


@pytest.mark.parametrize("table", TABLES)
def test_spend_tables_fail_closed_without_tenant(table: str, seeded: UUID) -> None:
    assert _app(None, f"SELECT count(*) FROM {table}")[0][0] == 0


@pytest.mark.parametrize("table", sorted(INSERTS))
def test_spend_tables_refuse_cross_tenant_writes(
    table: str, tenant_a: UUID, tenant_b: UUID
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        _app(tenant_a, INSERTS[table], {"t": tenant_b, "r": uuid4(), "h": H})


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("verb", ["UPDATE {t} SET tenant_id = tenant_id", "DELETE FROM {t}"])
def test_spend_tables_are_insert_only(table: str, verb: str, seeded: UUID) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _app(seeded, verb.format(t=table))


def test_the_tenant_role_cannot_read_the_operator_report(seeded: UUID) -> None:
    assert _app(seeded, "SELECT count(*) FROM spend_reports", role="public_reader")[0][0] == 1
    assert _app(seeded, "SELECT tenant_json FROM spend_reports", role="public_reader")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _app(seeded, "SELECT operator_json FROM spend_reports", role="public_reader")
    for table in ("run_cap_versions", "balance_snapshots", "prepulled_files", "spend_defects"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _app(seeded, f"SELECT count(*) FROM {table}", role="public_reader")
