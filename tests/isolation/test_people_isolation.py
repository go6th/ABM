"""Hard isolation gate for the people tables (migration 0014): every table is invisible across
tenants, refuses a cross-tenant write (WITH CHECK) and returns nothing without a tenant context."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from tests.conftest import ADMIN_DSN, APP_DSN

TABLES = (
    "people",
    "person_search_pages",
    "person_sources",
    "person_profiles",
    "person_posts",
    "person_post_pulls",
    "person_events",
    "people_pipeline_runs",
)

INSERTS = {
    "person_search_pages": (
        "INSERT INTO person_search_pages (tenant_id, account_id, run_id, source_id, page_no,"
        " page_ref, record_count, complete) VALUES (%(t)s, %(a)s, %(r)s, 's', 0, 'ref', 0, true)"
    ),
    "person_sources": (
        "INSERT INTO person_sources (tenant_id, account_id, run_id, person_key, source_record_id,"
        " origin) VALUES (%(t)s, %(a)s, %(r)s, 'pk', %(rid)s, 'title_search')"
    ),
    "person_profiles": (
        "INSERT INTO person_profiles (tenant_id, account_id, run_id, profile_url_canonical,"
        " person_key, provider_code, raw_ref, call_ref, certifications_provided) VALUES"
        " (%(t)s, %(a)s, %(r)s, %(rid)s, 'pk', 'p_people', 'raw', 'call', false)"
    ),
    "person_posts": (
        "INSERT INTO person_posts (tenant_id, account_id, run_id, profile_url_canonical, post_url,"
        " excerpt, excerpt_sha256, raw_ref, call_ref) VALUES (%(t)s, %(a)s, %(r)s, %(rid)s, 'u',"
        " '', repeat('0', 64), 'raw', 'call')"
    ),
    "person_post_pulls": (
        "INSERT INTO person_post_pulls (tenant_id, account_id, run_id, profile_url_canonical,"
        " post_count) VALUES (%(t)s, %(a)s, %(r)s, %(rid)s, 0)"
    ),
    "person_events": (
        "INSERT INTO person_events (tenant_id, account_id, run_id, person_key, kind, rule_id,"
        " actor) VALUES (%(t)s, %(a)s, %(r)s, 'pk', 'scope', 'r', 'pipeline')"
    ),
    "people_pipeline_runs": (
        "INSERT INTO people_pipeline_runs (run_id, tenant_id, account_id, as_of, config_hash,"
        " record_json) VALUES (%(r)s, %(t)s, %(a)s, current_date, repeat('a', 64), '{}')"
    ),
    "people": (
        "INSERT INTO people (tenant_id, account_id, run_id, provider_person_id, source_ref,"
        " scope_status) VALUES (%(t)s, %(a)s, %(r)s, %(rid)s, 'raw', 'unresolved')"
    ),
}


def _seed(tenant: UUID, account: UUID) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        for sql in INSERTS.values():
            c.execute(sql, {"t": tenant, "a": account, "r": uuid4(), "rid": uuid4().hex})


def _app(
    tenant: UUID | None, sql: str, params: dict[str, object] | None = None
) -> list[tuple[object, ...]]:
    with psycopg.connect(APP_DSN) as c, c.transaction():
        if tenant is not None:
            c.execute(
                "SELECT set_config('app.tenant_id', %s, true),"
                " set_config('app.scope', 'system', true)",
                (str(tenant),),
            )
        cur = c.execute(sql, params or {})
        return cur.fetchall() if cur.description else []


@pytest.fixture
def seeded(tenant_b: UUID, account_of_b: UUID) -> UUID:
    _seed(tenant_b, account_of_b)
    return account_of_b


@pytest.mark.parametrize("table", TABLES)
def test_people_tables_invisible_across_tenants(table: str, tenant_a: UUID, seeded: UUID) -> None:
    assert _app(tenant_a, f"SELECT count(*) FROM {table}")[0][0] == 0


@pytest.mark.parametrize("table", TABLES)
def test_people_tables_fail_closed_without_tenant(table: str, seeded: UUID) -> None:
    assert _app(None, f"SELECT count(*) FROM {table}")[0][0] == 0


@pytest.mark.parametrize("table", TABLES)
def test_people_tables_refuse_cross_tenant_writes(
    table: str, tenant_a: UUID, tenant_b: UUID, account_of_b: UUID
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        _app(
            tenant_a,
            INSERTS[table],
            {"t": tenant_b, "a": account_of_b, "r": uuid4(), "rid": uuid4().hex},
        )
