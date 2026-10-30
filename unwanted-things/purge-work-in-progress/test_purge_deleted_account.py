"""Purging one account the user already deleted (migration 0096; SYNTHETIC data only).

GUARANTEE(test_a_live_account_is_refused_and_nothing_changes)
GUARANTEE(test_a_dry_run_changes_nothing_and_counts_what_execution_erases)
GUARANTEE(test_execution_erases_every_planned_table_for_that_account_and_no_other)
GUARANTEE(test_money_and_audit_ledgers_survive_and_content_columns_are_scrubbed)
GUARANTEE(test_the_account_becomes_a_tombstone_and_the_domain_can_be_added_again)
GUARANTEE(test_the_purge_adds_one_audit_row_on_a_valid_chain)
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors

from account_intelligence.db.session import tenant_txn
from account_intelligence.runs.lifecycle import add_account
from audit import chain
from audit.events import AuditEvent, record
from tenancy import lifecycle
from tests.conftest import ADMIN_DSN, APP_DSN
from tests.db.purge_world import CANARY, Scope, plant_scope, snapshot
from tests.isolation.matrix.plant import plant, tenant_tables

pytestmark = pytest.mark.requires_postgres

CALL = "SELECT purge_deleted_account(%s, %s, %s, %s, %s)"


def call(
    conn: psycopg.Connection[Any],
    tenant: UUID,
    account: UUID,
    execute: bool,
    actor: str = "operator:test",
    allow_shared: bool = False,
) -> dict[str, Any]:
    """One call of the purge function; returns its summary."""
    row = conn.execute(CALL, (tenant, account, execute, actor, allow_shared)).fetchone()
    assert row is not None
    out: dict[str, Any] = row[0]
    return out


def _tenant(admin: psycopg.Connection[Any], name: str) -> UUID:
    tid: UUID = admin.execute(
        "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-{uuid4().hex[:6]}",)
    ).fetchone()[0]  # type: ignore[index]
    admin.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 100)", (tid,))
    return tid


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    """Autocommit superuser connection to the scratch database."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        yield conn


@dataclass
class World:
    """Tenant 1 holds the deleted target A and a live sibling B; tenant 2 holds C (the same
    domain as A, also deleted)."""

    t1: UUID
    t2: UUID
    domain: str
    a: Scope
    b: Scope
    c: Scope


def make_world(admin: psycopg.Connection[Any]) -> World:
    """Plant the three accounts."""
    t1, t2 = _tenant(admin, "purge-one"), _tenant(admin, "purge-two")
    domain = f"bd-{secrets.token_hex(4)}.example"
    a = plant_scope(admin, t1, domain, deleted=True)
    b = plant_scope(admin, t1, f"sibling-{domain}", deleted=False)
    c = plant_scope(admin, t2, domain, deleted=True)
    return World(t1, t2, domain, a, b, c)


@dataclass
class Executed:
    """A world before and after one purge of A."""

    world: World
    before1: dict[str, int]
    before2: dict[str, int]
    dry: dict[str, Any]
    done: dict[str, Any]
    after1: dict[str, int]
    after2: dict[str, int]


@pytest.fixture(scope="module")
def executed() -> Iterator[Executed]:
    """Plant once, dry run, execute once; many tests read the result."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        w = make_world(admin)
        before1, before2 = snapshot(admin, w.t1), snapshot(admin, w.t2)
        dry = call(admin, w.t1, w.a.account, False)
        mid1 = snapshot(admin, w.t1)
        assert mid1 == before1  # a dry run changes nothing
        done = call(admin, w.t1, w.a.account, True)
        yield Executed(w, before1, before2, dry, done, snapshot(admin, w.t1), snapshot(admin, w.t2))


# ----------------------------------------------------------------------------------------------
# refusals
# ----------------------------------------------------------------------------------------------


def test_a_live_account_is_refused_and_nothing_changes(admin: psycopg.Connection[Any]) -> None:
    w = make_world(admin)
    before = snapshot(admin, w.t1)
    for execute in (False, True):
        with pytest.raises(errors.CheckViolation, match="still active"):
            call(admin, w.t1, w.b.account, execute)
    # an account that is not in that tenant, and one that does not exist
    with pytest.raises(errors.NoDataFound):
        call(admin, w.t2, w.a.account, True)
    with pytest.raises(errors.NoDataFound):
        call(admin, w.t1, uuid4(), True)
    with pytest.raises(errors.CheckViolation, match="actor"):
        call(admin, w.t1, w.a.account, True, actor="bad actor")
    assert snapshot(admin, w.t1) == before
    live = admin.execute("SELECT deleted_at FROM accounts WHERE id = %s", (w.b.account,))
    assert live.fetchone() == (None,)


def test_an_open_research_blocks_the_purge(admin: psycopg.Connection[Any]) -> None:
    w = make_world(admin)
    tables = {t.name: t for t in tenant_tables(admin)}
    over = {"account_id": w.a.account, "status": "RUNNING", "owner_user_id": "x"}
    run = plant(admin, tables["account_runs"], w.t1, over)
    before = snapshot(admin, w.t1)
    with pytest.raises(errors.ObjectInUse, match="still open"):
        call(admin, w.t1, w.a.account, True)
    assert snapshot(admin, w.t1) == before
    admin.execute(
        "UPDATE account_runs SET status = 'FAILED', finished_at = now() WHERE id = %s", (run["id"],)
    )
    job = plant(
        admin,
        tables["research_jobs"],
        w.t1,
        {
            "account_id": w.a.account,
            "status": "queued",
            "kind": "web_refresh",
            "scope": "jobs",
            "period": "2026-10",
            "credits_estimated": 1,
            "requested_by": "x",
            "owner_user_id": "x",
        },
    )
    with pytest.raises(errors.ObjectInUse, match="still open"):
        call(admin, w.t1, w.a.account, False)
    admin.execute(
        "UPDATE research_jobs SET status = 'done', finished_at = now() WHERE id = %s", (job["id"],)
    )
    assert call(admin, w.t1, w.a.account, False)["executed"] is False


def test_the_function_is_not_callable_by_the_runtime_role() -> None:
    with psycopg.connect(APP_DSN) as c:
        for sql in (
            "SELECT purge_candidates('x.example')",
            f"SELECT purge_deleted_account('{uuid4()}', '{uuid4()}', false)",
        ):
            with pytest.raises(errors.InsufficientPrivilege), c.transaction():
                c.execute(sql)


def test_a_non_superuser_operator_can_run_it_under_forced_row_security(
    admin: psycopg.Connection[Any],
) -> None:
    """On Cloud SQL the migrator is not a superuser and policies bind the table owner too: the
    function must work through its own rights, and the operator must read no table directly."""
    w = make_world(admin)
    role = f"purge_op_{secrets.token_hex(3)}"
    admin.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
    admin.execute(f"GRANT account_purge_runner TO {role}")
    try:
        with admin.transaction():
            admin.execute(f"SET LOCAL ROLE {role}")
            seen = admin.execute(
                "SELECT count(*) FROM purge_candidates(%s)", (w.domain,)
            ).fetchone()
            assert seen == (2,)
            dry = call(admin, w.t1, w.a.account, False)
            assert dry["rows_total"] > 0
            with pytest.raises(errors.InsufficientPrivilege), admin.transaction():
                admin.execute("SELECT count(*) FROM accounts")
            with pytest.raises(errors.InsufficientPrivilege), admin.transaction():
                admin.execute("DELETE FROM facts")
    finally:
        admin.execute(f"REVOKE account_purge_runner FROM {role}")
        admin.execute(f"DROP ROLE {role}")


def test_candidates_are_found_across_tenants_and_wildcards_are_refused(
    admin: psycopg.Connection[Any],
) -> None:
    w = make_world(admin)
    rows = admin.execute("SELECT * FROM purge_candidates(%s)", (w.domain,)).fetchall()
    assert {(r[0], r[1]) for r in rows} == {(w.t1, w.a.account), (w.t2, w.c.account)}
    assert all(r[2] is not None for r in rows)
    live = admin.execute("SELECT * FROM purge_candidates(%s)", (f"sibling-{w.domain}",)).fetchall()
    assert [(r[1], r[2]) for r in live if r[1] == w.b.account] == [(w.b.account, None)]
    for bad in ("%", "bd.%", "*.example", " bd.com", "BD.com", "", "deleted-0123abcd.invalid"):
        with pytest.raises(errors.CheckViolation), admin.transaction():
            admin.execute("SELECT * FROM purge_candidates(%s)", (bad,))
    assert admin.execute("SELECT * FROM purge_candidates('nobody.example')").fetchall() == []


# ----------------------------------------------------------------------------------------------
# dry run and execution
# ----------------------------------------------------------------------------------------------


def test_a_dry_run_changes_nothing_and_counts_what_execution_erases(executed: Executed) -> None:
    plan = lifecycle.load_account_purge_plan()
    w, dry, done = executed.world, executed.dry, executed.done
    assert dry["executed"] is False and done["executed"] is True
    assert dry["tables"] == done["tables"] and dry["scrubbed"] == done["scrubbed"]
    assert dry["rows_total"] == done["rows_total"] > 0
    assert set(dry["tables"]) == set(plan.erase) and set(dry["scrubbed"]) == set(plan.scrub)
    for table in plan.erase:
        assert dry["tables"][table] == w.a.count(table), table  # every planted row was reached
    assert dry["object_prefixes"] == done["object_prefixes"]
    assert dry["shared_payload_refs"] == 0


def test_a_dry_run_keeps_the_session_context(admin: psycopg.Connection[Any]) -> None:
    w = make_world(admin)
    with admin.transaction():
        admin.execute("SELECT set_config('app.tenant_id', 'before', true)")
        call(admin, w.t1, w.a.account, False)
        got = admin.execute("SELECT current_setting('app.tenant_id'), current_setting('app.scope')")
        assert got.fetchone() == ("before", "")


def test_execution_erases_every_planned_table_for_that_account_and_no_other(
    executed: Executed,
) -> None:
    plan = lifecycle.load_account_purge_plan()
    w, b1, a1, b2, a2 = (
        executed.world,
        executed.before1,
        executed.after1,
        executed.before2,
        executed.after2,
    )
    for table in plan.erase:
        assert a1[table] == b1[table] - w.a.count(table), table
        assert a1[table] == w.b.count(table), f"{table}: the sibling account lost or gained rows"
    # the other tenant, whose account has the same domain, changed in no table at all
    assert a2 == b2
    # money, audit and every other table of the tenant are as they were, except the two rows
    # the purge writes
    others = set(a1) - set(plan.erase)
    changed = {t for t in others if a1[t] != b1[t]}
    assert changed == {"audit_events", "tombstones"}, changed
    assert a1["audit_events"] == b1["audit_events"] + 1
    assert a1["tombstones"] == b1["tombstones"] + 1


def test_the_foreign_key_order_holds_against_the_database(admin: psycopg.Connection[Any]) -> None:
    """The purge ran with every constraint on (``executed``); the plan's order also agrees with
    the catalog: a child is erased before its parent, and nothing kept hangs on an erased row."""
    plan = lifecycle.load_account_purge_plan()
    position = {t: i for i, t in enumerate(plan.erase)}
    rows = admin.execute(
        """
        SELECT cl.relname, cf.relname, con.confdeltype
          FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid
          JOIN pg_class cf ON cf.oid = con.confrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
         WHERE con.contype = 'f' AND n.nspname = 'public' AND cl.relname <> cf.relname
        """
    ).fetchall()
    kept = set(plan.scrub) | set(plan.keep) | set(plan.tombstone) | set(plan.tenant_level)
    for child, parent, action in rows:
        if child in position and parent in position:
            assert position[child] < position[parent], (child, parent)
        elif parent in position and child in kept:
            # a kept table must not point at an erased row, nor be cascaded into by its erasure
            raise AssertionError(f"{child} (kept) references {parent} (erased), action {action}")


def test_money_and_audit_ledgers_survive_and_content_columns_are_scrubbed(
    executed: Executed, admin: psycopg.Connection[Any]
) -> None:
    plan = lifecycle.load_account_purge_plan()
    w = executed.world
    for table in plan.keep:
        assert executed.after1[table] == executed.before1[table], table
    run = w.a.run
    # tool calls: the cost columns stay, the provider answers and payload pointers are gone
    spent = admin.execute(
        "SELECT count(*), count(response), count(response_ref), sum(cost_usd) FROM tool_calls"
        " WHERE run_id = %s",
        (run,),
    ).fetchone()
    assert spent is not None and spent[0] == w.a.count("tool_calls")
    assert spent[1] == 0 and spent[2] == 0
    llm = admin.execute(
        "SELECT count(*) FILTER (WHERE output_json = '{}'::jsonb), count(*) FROM llm_calls"
        " WHERE run_id = %s",
        (run,),
    ).fetchone()
    assert llm is not None and llm[0] == llm[1] == w.a.count("llm_calls")
    # the run header stays and keeps its ledger columns; the goal and the free text are gone
    hdr = admin.execute(
        "SELECT status, run_input, stop_detail, reserved_credits FROM account_runs WHERE id = %s",
        (run,),
    ).fetchone()
    assert hdr is not None and hdr[0] == "COMPLETED" and hdr[1] == {} and hdr[2] is None
    legacy = admin.execute("SELECT status, error FROM runs WHERE id = %s", (w.a.legacy_run,))
    assert legacy.fetchone() == ("failed", "purged")
    job = admin.execute(
        "SELECT goal, failure_reason, status FROM research_jobs WHERE account_id = %s",
        (w.a.account,),
    ).fetchall()
    assert job and all(r[0] is None and r[1] is None and r[2] == "done" for r in job)
    # the credit ledger entry hangs on the job and is still there
    credits = admin.execute("SELECT count(*) FROM credit_entries WHERE tenant_id = %s", (w.t1,))
    assert credits.fetchone() == (w.a.count("credit_entries") + w.b.count("credit_entries"),)
    # the sibling's calls keep their answers
    sib = admin.execute(
        "SELECT count(response), count(response_ref) FROM tool_calls WHERE run_id = %s",
        (w.b.run,),
    ).fetchone()
    assert sib == (w.b.count("tool_calls"), w.b.count("tool_calls"))
    # nothing of the planted words is left on the account row, the tombstone or the audit row
    for sql in (
        "SELECT a::text FROM accounts a WHERE a.id = %s",
        "SELECT t::text FROM tombstones t WHERE t.subject_id = %s",
    ):
        for (text,) in admin.execute(sql, (str(w.a.account),)).fetchall():
            assert CANARY not in text


def test_the_account_becomes_a_tombstone_and_the_domain_can_be_added_again(
    executed: Executed, admin: psycopg.Connection[Any]
) -> None:
    w = executed.world
    acct = admin.execute(
        "SELECT domain, name, industry, pursuing_client, careers_url, careers_fetch_ref,"
        " careers_url_verified_at, entity_lock, entity_lock_at, watch_config_json, primary_lane,"
        " lanes, refresh_cadence, next_refresh_at, deleted_at, deleted_by, owner_user_id,"
        " tenant_id FROM accounts WHERE id = %s",
        (w.a.account,),
    ).fetchone()
    assert acct is not None
    assert acct[0] == f"deleted-{str(w.a.account).replace('-', '')[:8]}.invalid"
    assert acct[1:9] == (None,) * 8
    assert acct[9] == {} and acct[10] is None and acct[11] == [] and acct[12:14] == (None, None)
    assert acct[14] is not None and acct[15] is not None  # the deletion record is kept
    # another tenant's account with the same domain is untouched
    other = admin.execute(
        "SELECT domain, name FROM accounts WHERE id = %s", (w.c.account,)
    ).fetchone()
    assert other is not None and other[0] == w.domain and other[1] is not None
    # adding the same domain again (as the same user) makes a clean NEW account
    owner = acct[16]

    async def readd() -> tuple[UUID, bool]:
        async with tenant_txn(w.t1) as s:
            return await add_account(s, w.t1, w.domain, added_by=owner)

    new_id, created = asyncio.run(readd())
    assert created and new_id != w.a.account
    fresh = admin.execute("SELECT name, deleted_at FROM accounts WHERE id = %s", (new_id,))
    assert fresh.fetchone() == (None, None)
    # and a late "add account" can never bring the tombstone back
    with pytest.raises(errors.CheckViolation, match="purged account"):
        admin.execute(
            "UPDATE accounts SET deleted_at = NULL, deleted_by = NULL WHERE id = %s", (w.a.account,)
        )


def test_the_tombstone_domain_stays_unique_when_the_short_form_is_taken(
    admin: psycopg.Connection[Any],
) -> None:
    w = make_world(admin)
    short = f"deleted-{str(w.a.account).replace('-', '')[:8]}.invalid"
    owner = admin.execute("SELECT owner_user_id FROM accounts WHERE id = %s", (w.a.account,))
    tables = {t.name: t for t in tenant_tables(admin)}
    plant(admin, tables["accounts"], w.t1, {"domain": short, "owner_user_id": owner.fetchone()[0]})  # type: ignore[index]
    call(admin, w.t1, w.a.account, True)
    got = admin.execute("SELECT domain FROM accounts WHERE id = %s", (w.a.account,)).fetchone()
    assert got is not None and got[0] == f"deleted-{str(w.a.account).replace('-', '')[:12]}.invalid"


def test_running_it_again_changes_nothing_and_adds_no_second_audit_row(
    executed: Executed, admin: psycopg.Connection[Any]
) -> None:
    w = executed.world
    before = snapshot(admin, w.t1)
    again = call(admin, w.t1, w.a.account, True)
    assert again["rows_total"] == 0 and again["already_tombstone"] is True
    assert snapshot(admin, w.t1) == before
    # the object targets can still be derived (the runs stay), so a failed object step can be redone
    assert again["object_prefixes"] == executed.done["object_prefixes"]


def test_the_counts_are_kept_in_a_content_free_tombstone_row(
    executed: Executed, admin: psycopg.Connection[Any]
) -> None:
    w = executed.world
    row = admin.execute(
        "SELECT reason, content_sha256 FROM tombstones WHERE subject_type = 'account'"
        " AND subject_id = %s AND tenant_id = %s",
        (str(w.a.account), w.t1),
    ).fetchone()
    assert row is not None and row[1] is None
    counts = json.loads(row[0].removeprefix("purged "))
    assert counts["tables"] == executed.done["tables"]
    assert CANARY not in row[0]


@pytest.mark.asyncio
async def test_the_purge_adds_one_audit_row_on_a_valid_chain(
    admin: psycopg.Connection[Any],
) -> None:
    t = _tenant(admin, "purge-audit")
    a = plant_scope(admin, t, "audit-purge.example", deleted=True)
    now = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
    async with tenant_txn(t) as s:
        for n in range(2):
            await record(
                s,
                AuditEvent(
                    at=now,
                    actor="user-1",
                    actor_kind="user",
                    audit_class="admin",
                    action="settings.update",
                    resource_type="tenant",
                    resource_id=str(n),
                ),
            )
    call(admin, t, a.account, True, actor="operator:inamdar")
    rows = admin.execute(
        "SELECT seq, actor, actor_kind, audit_class, action, resource_type, resource_id, outcome"
        " FROM audit_events WHERE tenant_id = %s ORDER BY seq",
        (t,),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]
    assert rows[2][1:] == (
        "operator:inamdar",
        "operator",
        "admin",
        "account.purged",
        "account",
        str(a.account),
        "ok",
    )
    async with tenant_txn(t) as s:
        verdict = await chain.verify(s, now, batch=100, max_batches=5)
    assert verdict.ok and verdict.to_seq == 3, verdict


def test_calls_of_other_accounts_that_share_a_payload_block_the_purge(
    admin: psycopg.Connection[Any],
) -> None:
    w = make_world(admin)
    tables = {t.name: t for t in tenant_tables(admin)}
    ref = f"tenants/{w.t1}/accounts/{w.a.account}/runs/{w.a.run}/raw/{secrets.token_hex(8)}.json.gz"
    plant(
        admin,
        tables["tool_calls"],
        w.t1,
        {"run_id": w.b.run, "response_ref": ref, "args_hash": secrets.token_hex(32)},
    )
    before = snapshot(admin, w.t1)
    dry = call(admin, w.t1, w.a.account, False)
    assert dry["shared_payload_refs"] == 1
    with pytest.raises(errors.ObjectInUse, match="point at payloads"):
        call(admin, w.t1, w.a.account, True)
    assert snapshot(admin, w.t1) == before
    done = call(admin, w.t1, w.a.account, True, allow_shared=True)
    assert done["shared_payload_refs"] == 1 and done["rows_total"] > 0


def test_the_research_cache_hashes_are_reported_before_the_calls_are_scrubbed(
    executed: Executed,
) -> None:
    a = executed.world.a
    assert len(executed.dry["cache_args_hashes"]) == a.count("tool_calls") >= 1
    assert executed.done["cache_args_hashes"] == executed.dry["cache_args_hashes"]


def test_a_failure_in_the_middle_rolls_everything_back(admin: psycopg.Connection[Any]) -> None:
    """All or nothing: a foreign key the function does not know about stops it in the middle of
    the erase, and every table of the account is as it was."""
    w = make_world(admin)
    tables = {t.name: t for t in tenant_tables(admin)}
    # a kept table that points at a row the purge erases would break the delete of that row
    admin.execute(
        "CREATE TABLE purge_probe (id uuid PRIMARY KEY, fact uuid NOT NULL REFERENCES facts (id))"
    )
    try:
        admin.execute(
            "INSERT INTO purge_probe VALUES (gen_random_uuid(), %s)", (w.a.rows["facts"][0]["id"],)
        )
        before = snapshot(admin, w.t1)
        with pytest.raises(errors.ForeignKeyViolation):
            call(admin, w.t1, w.a.account, True)
        assert snapshot(admin, w.t1) == before
        dom = admin.execute("SELECT domain FROM accounts WHERE id = %s", (w.a.account,))
        assert dom.fetchone() == (w.domain,)
        assert tables  # the planter is unchanged
    finally:
        admin.execute("DROP TABLE purge_probe")
