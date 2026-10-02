"""Sources table rules (data layer v1, step 9): stable codes, proven hashes, no deletes."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import select

from account_intelligence.db.models import Source
from tests.db.conftest import (
    make_account,
    make_run,
    make_source,
    make_tenant,
    set_tenant,
)


@pytest.fixture
def ctx(admin):  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    account = make_account(admin, tenant)
    return tenant, account, make_run(admin, tenant, account)


def test_valid_source_is_stored_with_its_code(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    source = make_source(admin, *ctx, code="S-0001")
    row = admin.execute("SELECT source_code FROM sources WHERE id = %s", (source,)).fetchone()
    assert row == ("S-0001",)


def test_codes_beyond_four_digits_are_valid(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    make_source(admin, *ctx, code="S-12345")


@pytest.mark.parametrize("code", ["S-1", "S-001", "s-0001", "S-ABCD", "X-0001", "S-0001 "])
def test_source_code_format_is_enforced(admin, ctx, code) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_sources_source_code_format"):
        make_source(admin, *ctx, code=code)


def test_code_is_unique_per_account_but_reusable_across_accounts(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    make_source(admin, *ctx, code="S-0001")
    with pytest.raises(psycopg.errors.UniqueViolation, match="uq_sources_account_id_source_code"):
        make_source(admin, *ctx, code="S-0001")
    other = make_account(admin, tenant)
    make_source(admin, tenant, other, make_run(admin, tenant, other), code="S-0001")


def test_url_is_unique_per_account(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    make_source(admin, *ctx, code="S-0001", url="https://example.com/a")
    with pytest.raises(psycopg.errors.UniqueViolation, match="uq_sources_account_id_url_hash"):
        make_source(admin, *ctx, code="S-0002", url="https://example.com/a")


def test_url_hash_must_be_the_sha256_of_the_normalized_url(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_sources_url_hash_matches"):
        admin.execute(
            "INSERT INTO sources (tenant_id, account_id, source_code, url, url_normalized,"
            " url_hash, kind, first_seen_run_id, last_seen_run_id)"
            " VALUES (%s, %s, 'S-0001', 'https://a.example.com/', 'https://a.example.com/',"
            " repeat('0', 64), 'primary', %s, %s)",
            (tenant, account, run, run),
        )


def test_kind_and_url_scheme_are_checked(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_sources_kind"):
        make_source(admin, *ctx, kind="rumour")
    with pytest.raises(psycopg.errors.CheckViolation, match="url_is_http"):
        make_source(admin, *ctx, url="ftp://files.example.com/report")


@pytest.mark.parametrize(
    "assignment",
    [
        "source_code = 'S-0002'",
        "url_normalized = 'https://other.example.com/'",
        "url_hash = encode(sha256(convert_to('https://other.example.com/', 'UTF8')), 'hex'),"
        " url_normalized = 'https://other.example.com/'",
    ],
)
def test_source_identity_cannot_change(admin, ctx, assignment) -> None:  # type: ignore[no-untyped-def]
    source = make_source(admin, *ctx)
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
        admin.execute(f"UPDATE sources SET {assignment} WHERE id = %s", (source,))


def test_metadata_and_last_seen_run_may_change(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    source = make_source(admin, *ctx)
    later = make_run(admin, tenant, account)
    admin.execute(
        "UPDATE sources SET title = 'Annual report', last_seen_run_id = %s WHERE id = %s",
        (later, source),
    )


def test_sources_are_never_deleted_even_by_the_owner(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    source = make_source(admin, *ctx)
    with pytest.raises(psycopg.errors.RestrictViolation, match="never deleted"):
        admin.execute("DELETE FROM sources WHERE id = %s", (source,))


def test_seen_in_runs_must_belong_to_the_same_account(admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, _ = ctx
    other = make_account(admin, tenant)
    foreign_run = make_run(admin, tenant, other)
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="first_seen_run_id_runs"):
        make_source(admin, tenant, account, foreign_run)


def test_sources_are_tenant_isolated(db, admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, _ = ctx
    make_source(admin, *ctx)
    set_tenant(db, tenant)
    assert db.execute(select(Source).where(Source.account_id == account)).scalar_one()
    set_tenant(db, make_tenant(admin))
    assert db.execute(select(Source).where(Source.account_id == account)).first() is None
