"""Tenant deletion covers every table that holds a tenant's data, or says why not (leakage review
2026-10-30, F3).

``config/deletion.yaml`` classifies each table that carries a ``tenant_id`` (or a ``thread_id``
the tenant prefixes) as exactly one of ``erase`` (the client's content: removed through the
approved, dry-run-first path), ``blocked`` (needs a decision first, with the reason) or ``never``
(kept on purpose: ledgers, billing, audit, configuration, with the reason). This test fails when
a new table is in none of them, when one is in two, when a name is stale, when a reason is
missing, when the erase order would break a foreign key, or when the database does not allow what
the plan lists. The stored objects (the five delivered files, workspace zips, raw uploads,
archived bodies) are deleted by prefix after the rows, through the object store port, and are
proved here with a hermetic fake.

GUARANTEE(test_every_tenant_table_is_classified_for_deletion)
GUARANTEE(test_deletion_removes_the_stored_objects_of_the_tenant_and_no_other)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from tenancy import lifecycle
from tenancy.storage import is_tenant_prefix, object_prefixes, tenant_of_key
from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeObjectStore
from tests.isolation.matrix.plant import tenant_tables

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _db_tables() -> set[str]:
    with psycopg.connect(ADMIN_DSN) as c:
        return {t.name for t in tenant_tables(c)}


@pytest.mark.requires_postgres
def test_every_tenant_table_is_classified_for_deletion() -> None:
    plan = lifecycle.load_plan()
    erase, blocked, never = set(plan.erase), set(plan.blocked), set(plan.never)
    tables = _db_tables()
    assert tables, "no tenant tables found"
    # no table in two lists
    assert not erase & blocked and not erase & never and not blocked & never
    # `blocked` also holds the stored objects, which is not a table
    names = erase | (blocked - {"raw_payload_objects"}) | never
    unclassified = sorted(tables - names)
    assert not unclassified, f"tables in none of erase, blocked, never: {unclassified}"
    stale = sorted(names - tables)
    assert not stale, f"names in config/deletion.yaml that are no table: {stale}"
    # every kept or held table says why
    assert all(len(r.strip()) >= 12 for r in plan.blocked.values()), "a blocked item has no reason"
    assert set(plan.never_reasons) == never, "every `never` table needs its reason"
    assert all(len(r.strip()) >= 12 for r in plan.never_reasons.values())


@pytest.mark.requires_postgres
def test_the_erase_order_never_breaks_a_foreign_key() -> None:
    plan = lifecycle.load_plan()
    position = {t: i for i, t in enumerate(plan.erase)}
    with psycopg.connect(ADMIN_DSN) as c:
        fks = c.execute(
            """
            SELECT cl.relname, cf.relname, con.confdeltype
              FROM pg_constraint con
              JOIN pg_class cl ON cl.oid = con.conrelid
              JOIN pg_class cf ON cf.oid = con.confrelid
              JOIN pg_namespace n ON n.oid = cl.relnamespace
             WHERE con.contype = 'f' AND n.nspname = 'public' AND cl.relname <> cf.relname
            """
        ).fetchall()
    for child, parent, action in fks:
        if parent in position and child in position:
            assert position[child] < position[parent], (child, parent)
        elif parent in position and action in ("a", "r"):
            # a table the plan keeps must not point at a row the plan removes
            raise AssertionError(f"{child} (kept) references {parent} (erased)")


@pytest.mark.requires_postgres
def test_the_database_allows_exactly_what_the_plan_erases() -> None:
    plan = lifecycle.load_plan()
    with psycopg.connect(ADMIN_DSN) as c:
        for table in plan.erase:
            granted = c.execute(
                "SELECT has_table_privilege('tenant_eraser', %s, 'DELETE')", (table,)
            ).fetchone()
            assert granted and granted[0], f"the eraser cannot delete from {table}"
            policy = c.execute(
                "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = %s"
                " AND cmd IN ('ALL', 'DELETE')",
                (table,),
            ).fetchone()
            assert policy and policy[0] >= 1, f"no row policy lets a tenant delete from {table}"
        # a ledger is not erasable even by a call that names it
        for table in ("wallet_ledger", "audit_events", "account_runs", "accounts", "users"):
            assert table not in plan.erase
            granted = c.execute(
                "SELECT has_table_privilege('tenant_eraser', %s, 'DELETE')", (table,)
            ).fetchone()
            assert granted and not granted[0], f"the eraser can delete from {table}"


def test_object_prefixes_cover_every_layout_and_only_one_tenant() -> None:
    t, other = uuid4(), uuid4()
    prefixes = object_prefixes(t)
    assert all(is_tenant_prefix(p) for p in prefixes)
    assert len(prefixes) == 5 and len(set(prefixes)) == 5
    for p in prefixes:
        assert str(t) in p and str(other) not in p
    # the layouts the platform writes are all under one of them
    keys = [
        f"tenants/{t}/raw/{'a' * 64}",
        f"tenants/{t}/accounts/{uuid4()}/archive/{'b' * 64}.json.gz",
        f"tenants/{t}/accounts/{uuid4()}/runs/{uuid4()}/raw/{'c' * 64}.json.gz",
        f"{t}/{uuid4()}/{uuid4()}/deliverables/{'d' * 64}.docx",
        f"uploads/tenants/{t}/x",
        f"quarantine/tenants/{t}/y",
        f"quarantine/{t}/z",
    ]
    for key in keys:
        assert any(key.startswith(p) for p in prefixes), key
        assert tenant_of_key(key) == t
    # nothing wider than one tenant's tree is a deletable prefix
    for wide in ("", "tenants/", "uploads/", f"{str(t)[:8]}", "quarantine/", f"tenants/{t}"):
        assert not is_tenant_prefix(wide), wide


async def _delete_all(store: FakeObjectStore, tenant: UUID, batch: int) -> tuple[int, bool]:
    return await lifecycle.erase_objects(store, tenant, batch=batch)


@pytest.mark.asyncio
async def test_deleting_objects_is_batched_bounded_and_one_tenant_only() -> None:
    mine, theirs = uuid4(), uuid4()
    store = FakeObjectStore()
    for i in range(7):
        await store.put_if_absent(f"tenants/{mine}/raw/{i}", b"x", "text/plain")
        await store.put_if_absent(f"{mine}/{uuid4()}/{uuid4()}/deliverables/{i}.docx", b"x", "a/b")
        await store.put_if_absent(f"tenants/{theirs}/raw/{i}", b"x", "text/plain")
    await store.put_if_absent(f"{theirs}/{uuid4()}/{uuid4()}/deliverables/z.docx", b"x", "a/b")
    deleted, clean = await _delete_all(store, mine, batch=3)
    assert deleted == 14 and clean
    assert not [k for k in store.objects if str(mine) in k]
    assert len([k for k in store.objects if str(theirs) in k]) == 8  # untouched
    again, clean = await _delete_all(store, mine, batch=3)
    assert (again, clean) == (0, True)  # repeating is harmless


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_deletion_removes_the_stored_objects_of_the_tenant_and_no_other() -> None:
    from tests.phase3.test_ws1_tenancy import _count, _plant_erasable, _tenant

    plan = lifecycle.load_plan()
    mine, theirs = _tenant("obj-a"), _tenant("obj-b")
    planted = _plant_erasable(mine, plan)
    assert set(planted) >= set(plan.erase)
    store = FakeObjectStore()
    keys_mine = [
        f"tenants/{mine}/raw/{'a' * 64}",
        f"{mine}/{uuid4()}/{uuid4()}/deliverables/{'d' * 64}.xlsx",
        f"tenants/{mine}/accounts/{uuid4()}/archive/{'b' * 64}.json.gz",
    ]
    for k in keys_mine:
        await store.put_if_absent(k, b"body", "application/octet-stream")
    other = f"tenants/{theirs}/raw/{'e' * 64}"
    await store.put_if_absent(other, b"body", "application/octet-stream")
    async with tenant_txn(mine) as s:
        req = await lifecycle.request_deletion(
            s, actor="op-1", plane="operator", reason_code="tenant_request", now=NOW
        )
        await lifecycle.dry_run(s, req, actor="op-1", now=NOW)
        await lifecycle.approve(s, req, actor="op-2", now=NOW)
    # without an object store the objects stay and the item stays blocked
    async with tenant_txn(mine) as s:
        out = await lifecycle.execute(s, req, actor="op-2", now=NOW, plan=plan)
    assert out.exit_reason in ("blocked", "batch_cap")
    assert set(keys_mine) <= set(store.objects)
    # with the store: rows first, then the objects; the other tenant keeps its object
    reasons = []
    for _ in range(40):  # LOOP(test): cap=40 passes; exit=any result but batch_cap
        async with tenant_txn(mine) as s:
            out = await lifecycle.execute(s, req, actor="op-2", now=NOW, plan=plan, objects=store)
        reasons.append(out.exit_reason)
        if out.exit_reason != "batch_cap":
            break
    assert reasons[-1] == "blocked", reasons  # accounts, runs, users... still need a decision
    assert not set(keys_mine) & set(store.objects)
    assert other in store.objects
    for table in plan.erase:
        assert _count(mine, table) == 0, table
        assert _count(theirs, table) == 0  # nothing was ever planted for the other tenant
    async with tenant_txn(mine) as s:
        events = (
            await s.execute(
                lifecycle.text(
                    "SELECT detail_json FROM tenant_events WHERE event = 'deletion_blocked'"
                    " ORDER BY seq DESC LIMIT 1"
                )
            )
        ).scalar_one()
    assert "raw_payload_objects" not in events["blocked"]
    # the rows still held (the planted sources) keep the request open, with their reason
    assert set(events["blocked"]) == {"sources"}
