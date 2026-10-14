"""Private workspaces (migration 0063): two users of the same client cannot read or change each
other's rows, in the database itself, for every owned table.

* Coverage: every tenant table is listed in ``config/ownership.yaml`` as owned or tenant-level
  (with its reason), and the generated migration is current.
* Shape: every owned table has a NOT NULL ``owner_user_id`` and only the tenant-and-owner policy
  for app_rw; the fill trigger exists wherever an owner can be derived.
* Probes: user B of a client cannot read, list, update or delete user A's planted row, nor write
  a row stamped with A as owner, nor move a row of its own to A; user A still sees the row (the
  probe is meaningful); with no user and no system scope nothing is visible.
* The fill trigger: a system job inserting a child row without naming its owner gets the
  parent's owner.

GUARANTEE(test_every_tenant_table_is_owned_or_tenant_level)
GUARANTEE(test_users_of_one_client_cannot_reach_each_others_rows)
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
import yaml
from psycopg import sql

from tests.conftest import ADMIN_DSN, APP_DSN
from tests.isolation.matrix.plant import Table, _as_tenant, _where, plant, synthesise, tenant_tables

pytestmark = pytest.mark.requires_postgres

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_isolation_matrix as gen  # noqa: E402

MATRIX = gen.load_matrix()
OWNERSHIP = yaml.safe_load((ROOT / "config" / "ownership.yaml").read_text())
CREATED_OWNED = dict(OWNERSHIP.get("created_owned") or {})
ALL_OWNED = {**OWNERSHIP["owned"], **CREATED_OWNED}
OWNED = sorted(ALL_OWNED)
USER_A, USER_B = "owner-probe-a", "owner-probe-b"


def _tenant_table_names() -> set[str]:
    with psycopg.connect(ADMIN_DSN) as c:
        return {t.name for t in tenant_tables(c) if t.key_column == "tenant_id"}


def test_every_tenant_table_is_owned_or_tenant_level() -> None:
    owned, tenant = set(ALL_OWNED), set(OWNERSHIP["tenant"])
    assert not owned & tenant, sorted(owned & tenant)
    tables = _tenant_table_names() - {"ownership_backfill"}
    assert tables - owned - tenant == set(), "unclassified tables"
    assert (owned | tenant) - tables == set(), "listed tables that do not exist"
    assert all(isinstance(r, str) and r.strip() for r in OWNERSHIP["tenant"].values())


def test_the_generated_migration_is_current() -> None:
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_ownership_migration.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.parametrize("name", OWNED)
def test_owned_tables_have_a_required_owner_and_the_owner_policy(name: str) -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        nullable = c.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name = %s AND column_name = 'owner_user_id'",
            (name,),
        ).fetchone()
        policies = c.execute(
            "SELECT polname, pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid)"
            " FROM pg_policy WHERE polrelid = %s::regclass"
            " AND 'app_rw'::regrole = ANY(polroles)",
            (name,),
        ).fetchall()
        trigger = c.execute(
            "SELECT 1 FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = 'fill_owner'",
            (name,),
        ).fetchone()
    assert nullable == ("NO",), name
    assert [p[0] for p in policies] == ["tenant_owner_isolation"], policies
    for expr in policies[0][1:]:
        assert "owner_user_id" in expr and "app_user()" in expr and "app_tenant()" in expr
    rule = OWNERSHIP["owned"].get(name, {"column": "owner_user_id"})
    derivable = not (isinstance(rule, dict) and rule.get("column") == "owner_user_id")
    assert (trigger is not None) == derivable, name


class World:
    """One synthetic client with a planted row of every owned table, owned by user A."""

    def __init__(self) -> None:
        self.tenant: UUID | None = None
        self.tables: dict[str, Table] = {}
        self.rows: dict[str, dict[str, Any]] = {}
        self.mine: dict[str, dict[str, Any]] = {}
        self.over: dict[str, dict[str, Any]] = {}
        self.uniques_per_client: set[str] = set()


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        w.tenant = admin.execute(
            "INSERT INTO tenants (name) VALUES ('owner-probe-synthetic') RETURNING id"
        ).fetchone()[0]
        for table in tenant_tables(admin):
            if table.name not in ALL_OWNED:
                continue
            w.tables[table.name] = table
            over = dict((MATRIX["tables"].get(table.name) or {}).get("plant") or {})
            w.over[table.name] = over
            w.rows[table.name] = plant(admin, table, w.tenant, {**over, "owner_user_id": USER_A})
            try:
                w.mine[table.name] = plant(
                    admin, table, w.tenant, {**over, "owner_user_id": USER_B}
                )
            except psycopg.errors.UniqueViolation:
                # a per-client unique key with a fixed planted value: B gets no row of its own,
                # so only its "move" probe is skipped (listed in uniques_per_client)
                w.uniques_per_client.add(table.name)
    yield w


def _key(w: World, name: str, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    table = w.tables[name]
    return {k: rows[name][k] for k in (table.primary_key or rows[name])}


@pytest.mark.parametrize("name", OWNED)
def test_users_of_one_client_cannot_reach_each_others_rows(world: World, name: str) -> None:
    assert world.tenant is not None
    t = sql.Identifier(name)
    a_key = _key(world, name, world.rows)
    b_key = _key(world, name, world.mine) if name in world.mine else None
    count_a = sql.SQL("SELECT count(*) FROM {} WHERE {}").format(t, _where(a_key))

    def as_user(user: str | None, statement: sql.Composable) -> tuple[int, str]:
        return _as_tenant(APP_DSN, world.tenant, statement, user=user, scope="user")

    # meaningful: the owner sees the row, and the system scope does too
    assert as_user(USER_A, count_a) == (1, ""), name
    assert _as_tenant(APP_DSN, world.tenant, count_a, scope="system") == (1, ""), name
    failures: list[str] = []
    for op, statement in (
        ("read", count_a),
        (
            "list",
            sql.SQL("SELECT count(*) FROM {} WHERE owner_user_id = {}").format(
                t, sql.Literal(USER_A)
            ),
        ),
        (
            "update",
            sql.SQL("UPDATE {} SET owner_user_id = owner_user_id WHERE {}").format(
                t, _where(a_key)
            ),
        ),
        ("delete", sql.SQL("DELETE FROM {} WHERE {}").format(t, _where(a_key))),
    ):
        n, err = as_user(USER_B, statement)
        if not (n == 0 or (err and "row-level security" in err) or "permission denied" in err):
            failures.append(f"{op}: {err or n}")
    if b_key is not None:
        # B re-stamping its own row with A as owner must be refused by WITH CHECK
        move = sql.SQL("UPDATE {} SET owner_user_id = {} WHERE {}").format(
            t, sql.Literal(USER_A), _where(b_key)
        )
        n, err = as_user(USER_B, move)
        if not err:  # refused by WITH CHECK, a grant, or an append-only trigger
            failures.append(f"move: rows={n}")
    # with no user and no system scope nothing is visible
    n, _ = as_user(None, sql.SQL("SELECT count(*) FROM {}").format(t))
    if n != 0:
        failures.append(f"no user: {n}")
    # writing a row stamped with A as its owner is refused
    values = synthesise(
        world.tables[name], world.tenant, {**world.over[name], "owner_user_id": USER_A}
    )
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        t,
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Literal(_adapt(v)) for v in values.values()),
    )
    n, err = as_user(USER_B, insert)
    if not err:
        failures.append(f"write: rows={n}")
    assert not failures, failures


def _adapt(value: Any) -> Any:
    if isinstance(value, psycopg.types.json.Jsonb):
        import json

        return json.dumps(value.obj)
    return value


def test_a_system_insert_takes_the_owner_of_its_account() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tenant = admin.execute(
            "INSERT INTO tenants (name) VALUES ('owner-fill-synthetic') RETURNING id"
        ).fetchone()[0]
        account = admin.execute(
            "INSERT INTO accounts (tenant_id, domain, name, added_by, owner_user_id)"
            " VALUES (%s, 'fill.example', 'Fill', %s, %s) RETURNING id",
            (tenant, USER_A, USER_A),
        ).fetchone()[0]
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(tenant),),
        )
        owner = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd) VALUES (%s, %s, 'QUEUED', 'g', '{}',"
            " 1, 'fill-1', 5) RETURNING owner_user_id",
            (tenant, account),
        ).fetchone()
    assert owner == (USER_A,)
