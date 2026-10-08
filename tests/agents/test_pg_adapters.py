"""Agent adapters on the real scratch Postgres (migration 0041): RLS isolation, insert-only
tables, the mode and lineage constraints. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy.exc import IntegrityError

from agents.adapters import PgConsentStore, PgManifestStore, PgSenseChecks
from agents.manifest import ManifestRow, PullManifest
from agents.ports import RunContext, SenseCheckRecord
from tests.conftest import ADMIN_DSN, APP_DSN, _make_account

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 10, 8, 9, tzinfo=UTC)


def make_run(tenant: UUID, account: UUID, mode: str = "pull", parent: UUID | None = None) -> UUID:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        row = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key, run_mode, parent_run_id) VALUES"
            " (%s, %s, 'test', '{}', 1, %s, %s, %s) RETURNING id",
            (tenant, account, "k-" + uuid4().hex[:10], mode, parent),
        ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def manifest(tenant: UUID, run: UUID, key: str) -> PullManifest:
    return PullManifest(
        tenant_id=tenant,
        run_id=run,
        account_key=key,
        config_version="sk-1",
        created_at=T0,
        rows=(
            ManifestRow(
                row_key="company_news",
                status="stored",
                payload_refs=("pl/1",),
                call_refs=("c",),
                pulled_at=T0,
            ),
        ),
    )


async def test_pg_manifests_are_isolated_and_insert_only(tenant_a: UUID, tenant_b: UUID) -> None:
    key = f"a-{uuid4().hex[:6]}.example.com"
    run = make_run(tenant_a, _make_account(tenant_a, key))
    store = PgManifestStore()
    m = manifest(tenant_a, run, key)
    await store.save(m)
    await store.save(m)  # replay: no second row, no error
    assert await store.for_run(tenant_a, run) == m
    assert (await store.latest(tenant_a, key)) == m
    assert await store.for_run(tenant_b, run) is None  # RLS
    assert await store.latest(tenant_b, key) is None
    dsn = f"{APP_DSN}?options=-c%20app.tenant_id%3D{tenant_a}"
    with psycopg.connect(dsn) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("UPDATE pull_manifests SET digest = digest WHERE run_id = %s", (run,))


async def test_pg_manifest_cannot_point_at_another_tenants_run(
    tenant_a: UUID, tenant_b: UUID
) -> None:
    key = f"b-{uuid4().hex[:6]}.example.com"
    foreign = make_run(tenant_b, _make_account(tenant_b, key))
    with pytest.raises(
        IntegrityError
    ):  # composite FK (tenant_id, run_id): no such run for tenant A
        await PgManifestStore().save(manifest(tenant_a, foreign, key))


async def test_pg_consent_is_newest_first_and_revocation_is_a_row(
    tenant_a: UUID, tenant_b: UUID
) -> None:
    store = PgConsentStore()
    assert await store.current(tenant_a, "people_data") is None
    await store.record(tenant_a, "admin-synthetic", "v1", frozenset({"people_data"}), T0)
    got = await store.current(tenant_a, "people_data")
    assert got is not None and got.text_version == "v1" and got.revoked_at is None
    assert await store.current(tenant_b, "people_data") is None
    await store.record(
        tenant_a, "admin-synthetic", "v1", frozenset({"people_data"}), T0, revoked=True
    )
    got = await store.current(tenant_a, "people_data")
    assert got is not None and got.revoked_at == T0


async def test_pg_sense_checks_append_and_bind_the_latest(tenant_a: UUID) -> None:
    run = make_run(tenant_a, _make_account(tenant_a, f"c-{uuid4().hex[:6]}.example.com"), "full")
    ctx = RunContext(tenant_a, run, "c", "C", "c.example", False, "sk-1")
    store = PgSenseChecks()
    assert await store.sense_check(ctx) is None
    await store.save_sense_check(ctx, SenseCheckRecord(run, "a" * 64, "b" * 64, 2, 1))
    await store.save_sense_check(ctx, SenseCheckRecord(run, "a" * 64, "c" * 64, 1, 0))
    latest = await store.sense_check(ctx)
    assert latest is not None and (latest.facts_digest, latest.findings) == ("c" * 64, 1)


async def test_mode_and_lineage_constraints(tenant_a: UUID, tenant_b: UUID) -> None:
    acct = _make_account(tenant_a, f"d-{uuid4().hex[:6]}.example.com")
    pull = make_run(tenant_a, acct, "pull")
    make_run(tenant_a, acct, "build", pull)
    for mode, parent in (("build", None), ("pull", pull), ("phase1", None)):
        with pytest.raises(psycopg.errors.CheckViolation):
            make_run(tenant_a, acct, mode, parent)
    other = make_run(tenant_b, _make_account(tenant_b, f"e-{uuid4().hex[:6]}.example.com"))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        make_run(tenant_a, acct, "build", other)  # a build's parent is always the same tenant
