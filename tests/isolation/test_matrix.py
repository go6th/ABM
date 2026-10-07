"""The generated isolation matrix (TN1, TN3, TN4). SYNTHETIC tenants A (owner), B (attacker) and
C (bystander) each get a canary row in EVERY tenant table, discovered from the catalog.

* Tables: as tenant B (``app_rw``, transaction-local context), every read, list, update, delete,
  write and move against A's canary is hidden or refused; without a context nothing is visible.
* Views: ``security_invoker`` and no row of A visible to B.
* Definer functions, storage layouts, cache keys, events and jobs: each entry is checked.
* Routes: the authorisation layer is replaced by a fixed tenant-B principal (TN3: the database
  must hold on its own) and every route is called with A's identifiers; no reply carries any
  value of A's canaries.
* Coverage: every discovered object has a matrix entry and every entry an object; a new table
  without an entry, and a deliberately broken policy, are both caught.

GUARANTEE(test_every_discovered_object_is_covered_by_the_matrix)
GUARANTEE(test_cross_tenant_table_operations_are_refused)
GUARANTEE(test_a_broken_policy_is_caught_by_the_probes)
GUARANTEE(test_routes_leak_nothing_with_the_authorisation_layer_disabled)
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest

from tests.conftest import ADMIN_DSN, APP_DSN
from tests.isolation.matrix.plant import CANARY_PREFIX, Table, plant, probe, tenant_tables

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_isolation_matrix as gen  # noqa: E402

pytestmark = pytest.mark.requires_postgres
MATRIX = gen.load_matrix()


class World:
    """Three synthetic tenants and every table's canary rows for each."""

    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}
        self.tenants: dict[str, UUID] = {}
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def canary_values(self, who: str) -> set[str]:
        """Every planted text and identifier of one tenant (what must never cross)."""
        out: set[str] = {str(self.tenants[who])}
        for (_table, owner), values in self.rows.items():
            if owner != who:
                continue
            for v in values.values():
                if isinstance(v, UUID) or (isinstance(v, str) and CANARY_PREFIX in v):
                    out.add(str(v))
        return out


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        for who in ("A", "B", "C"):
            w.tenants[who] = admin.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"matrix-{who}-synthetic",)
            ).fetchone()[0]
        for table in tenant_tables(admin):
            w.tables[table.name] = table
            over = (MATRIX["tables"].get(table.name) or {}).get("plant")
            for who in ("A", "B", "C"):
                w.rows[(table.name, who)] = plant(admin, table, w.tenants[who], over)
    yield w


def test_every_discovered_object_is_covered_by_the_matrix() -> None:
    report = gen.compare(gen.discover(ADMIN_DSN), MATRIX)
    assert report.ok, {"uncovered": report.uncovered, "stale": report.stale}


@pytest.mark.parametrize("name", sorted(MATRIX["tables"]))
def test_cross_tenant_table_operations_are_refused(world: World, name: str) -> None:
    table = world.tables[name]
    over = (MATRIX["tables"].get(name) or {}).get("plant")
    a, b = world.tenants["A"], world.tenants["B"]
    a_key = {k: world.rows[(name, "A")][k] for k in (table.primary_key or world.rows[(name, "A")])}
    b_key = {k: world.rows[(name, "B")][k] for k in (table.primary_key or world.rows[(name, "B")])}
    outcomes = probe(APP_DSN, table, a_key, a, b, b_key, over)
    failed = [o for o in outcomes if not o.ok]
    assert not failed, failed
    assert {o.op for o in outcomes} >= {
        "read",
        "list",
        "unset_context",
        "update",
        "delete",
        "write",
    }


def test_each_tenant_still_sees_its_own_canary(world: World) -> None:
    """The probes are meaningful only if RLS lets the owner read its own row."""
    b = world.tenants["B"]
    for name in ("account_runs", "facts", "people", "deliverables", "memberships"):
        table = world.tables[name]
        key = {k: world.rows[(name, "B")][k] for k in table.primary_key}
        with psycopg.connect(APP_DSN) as c, c.transaction():
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(b),))
            where = " AND ".join(f"{k} = %s" for k in key)
            n = c.execute(f"SELECT count(*) FROM {name} WHERE {where}", list(key.values()))  # noqa: S608
            assert n.fetchone()[0] == 1, name


@pytest.mark.parametrize("name", sorted(MATRIX["views"]))
def test_views_run_as_the_invoker_and_hide_other_tenants(world: World, name: str) -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        opts = c.execute(
            "SELECT coalesce(reloptions, '{}') FROM pg_class WHERE relname = %s", (name,)
        ).fetchone()[0]
    assert "security_invoker=true" in opts or "security_invoker=on" in opts, (name, opts)
    col = MATRIX["views"][name]["tenant_column"]
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(world.tenants["B"]),))
        try:
            n = c.execute(
                f"SELECT count(*) FROM {name} WHERE {col} <> %s",  # noqa: S608 - catalog names
                (str(world.tenants["B"]),),
            ).fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            n = 0
    assert n == 0


def test_partition_children_have_no_runtime_grant() -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT c.relname FROM pg_class c WHERE c.relispartition"
            " AND has_table_privilege('app_rw', c.oid, 'SELECT')"
        ).fetchall()
    assert rows == []


@pytest.mark.parametrize("name", sorted(MATRIX["functions"]))
def test_definer_functions_are_owned_narrowly_and_reviewed(name: str) -> None:
    with psycopg.connect(ADMIN_DSN) as c:
        row = c.execute(
            "SELECT r.rolbypassrls, p.proconfig FROM pg_proc p JOIN pg_roles r"
            " ON r.oid = p.proowner WHERE p.proname = %s AND p.prosecdef",
            (name,),
        ).fetchone()
    assert row is not None
    _, config = row
    assert config and any(c.startswith("search_path=") for c in config), name
    proof = MATRIX["functions"][name]["proof"]
    assert (ROOT / proof).exists(), proof
    migrations = "\n".join(p.read_text() for p in (ROOT / "migrations").glob("*.sql"))
    assert f"{name}(" in migrations and "-- reviewed:" in migrations


def test_a_broken_policy_is_caught_by_the_probes(world: World) -> None:
    """A scratch table with a permissive policy (USING true) must fail the probes, and the
    coverage check must report it as uncovered."""
    name = f"tn1_broken_{uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(
            f"CREATE TABLE {name} (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            " tenant_id uuid NOT NULL, note text NOT NULL)"
        )
        try:
            admin.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
            admin.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
            admin.execute(
                f"CREATE POLICY broken ON {name} TO app_rw USING (true) WITH CHECK (true)"
            )
            admin.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO app_rw")
            table = next(t for t in tenant_tables(admin) if t.name == name)
            a, b = world.tenants["A"], world.tenants["B"]
            ka = plant(admin, table, a)
            kb = plant(admin, table, b)
            outcomes = probe(APP_DSN, table, {"id": ka["id"]}, a, b, {"id": kb["id"]})
            caught = {o.op for o in outcomes if not o.ok}
            assert {"read", "list", "update", "delete", "write", "move"} <= caught, outcomes
            report = gen.compare(gen.discover_db(ADMIN_DSN), MATRIX)
            assert name in report.uncovered["tables"]
        finally:
            admin.execute(f"DROP TABLE {name}")


@pytest.mark.parametrize("name", sorted(MATRIX["storage"]))
def test_storage_layouts_resolve_to_their_owner(world: World, name: str) -> None:
    from datetime import UTC, datetime, timedelta

    from tenancy.catalog import STORAGE_LAYOUTS
    from tenancy.storage import PathRefused, sign_download, tenant_of_key, verify_download

    a, b = world.tenants["A"], world.tenants["B"]
    key = STORAGE_LAYOUTS[name].sample(a)
    assert tenant_of_key(key) == a and tenant_of_key(key) != b
    now = datetime(2026, 1, 1, tzinfo=UTC)
    token = sign_download(b"k" * 32, a, key, "a" * 64, now, timedelta(minutes=5))
    with pytest.raises(PathRefused):
        verify_download(b"k" * 32, token, b, now)
    with pytest.raises(PathRefused):
        sign_download(b"k" * 32, b, key, "a" * 64, now, timedelta(minutes=5))


@pytest.mark.parametrize("name", sorted(MATRIX["cache"]))
def test_cache_keys_are_tenant_prefixed(world: World, name: str) -> None:
    from tenancy.keys import cache_key, tenant_of_cache_key

    a, b = world.tenants["A"], world.tenants["B"]
    ka, kb = cache_key(a, name, "x"), cache_key(b, name, "x")
    assert ka != kb and tenant_of_cache_key(ka) == a and tenant_of_cache_key(kb) == b


@pytest.mark.parametrize("name", sorted(MATRIX["events"]))
def test_events_are_carried_by_a_probed_table_or_a_worker_route(name: str) -> None:
    entry = MATRIX["events"][name]
    if "table" in entry:
        assert entry["table"] in MATRIX["tables"]
    else:
        assert MATRIX["routes"][entry["route"]]["probe"] == "machine"


@pytest.mark.parametrize("name", sorted(MATRIX["jobs"]))
def test_jobs_run_inside_the_tenant_boundary(name: str) -> None:
    entry = MATRIX["jobs"][name]
    assert entry["via"] == "tenant_txn" or entry["via"] in MATRIX["functions"]
    assert (ROOT / entry["proof"]).exists()


# ---------------------------------------------------------------- routes (authorisation off)


def _value(world: World, spec: str) -> str:
    if spec == "owner":
        return str(world.tenants["A"])
    table, column = spec.split(".")
    return str(world.rows[(table, "A")][column])


def _app(world: World) -> Any:
    """The real application over the faked tenant plane, with the authorisation layer replaced
    by a fixed tenant-B principal (a fresh step-up, so step-up never hides a route)."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from account_intelligence.api.auth import Identity, identity
    from account_intelligence.api.operator import OperatorIdentity, require_operator
    from tests.phase3.conftest import make_app

    b = world.tenants["B"]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (%s, 'matrix-b', 'admin')"
            " ON CONFLICT (tenant_id, user_id) DO NOTHING",
            (b,),
        )
    plane = make_app()
    app = plane.app
    extras = SimpleNamespace(wallet=None, upload_store=None, scanner=None, upload_parser=None)
    app.state.container = _Merged(app.state.container, extras)
    now = datetime.now(UTC)
    plane.clock.current = now

    async def as_b() -> Identity:
        return Identity("matrix-b", b, "admin", now)

    async def operator_b() -> OperatorIdentity:
        return OperatorIdentity("matrix-op", b, "matrix-session", now)

    app.dependency_overrides[identity] = as_b
    app.dependency_overrides[require_operator] = operator_b
    return app


class _Merged:
    """The test container plus the optional v2 ports a route may look up."""

    def __init__(self, base: Any, extra: Any) -> None:
        self.__dict__["_base"], self.__dict__["_extra"] = base, extra

    def __getattr__(self, name: str) -> Any:
        if hasattr(self._extra, name):
            return getattr(self._extra, name)
        return getattr(self._base, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._base, name, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(MATRIX["routes"]))
async def test_routes_leak_nothing_with_the_authorisation_layer_disabled(
    world: World, route: str
) -> None:
    os.environ.pop("ALLOW_UNAUTHENTICATED_TASKS", None)
    entry = MATRIX["routes"][route]
    method, template = route.split(" ", 1)
    path = template
    for param, spec in (entry.get("params") or {}).items():
        path = path.replace("{" + param + "}", _value(world, spec))
    body = entry.get("body")
    if body is not None:
        body = {
            k: (
                _value(world, v)
                if isinstance(v, str)
                and (v == "owner" or "." in v and v.split(".")[0] in world.tables)
                else v
            )
            for k, v in body.items()
        }
    headers = {"Idempotency-Key": f"matrix-{uuid4().hex[:16]}"} if entry.get("idempotent") else {}
    query = {
        k: _value(world, v) if "." in str(v) else v for k, v in (entry.get("query") or {}).items()
    }
    form = {
        k: _value(world, v) if "." in str(v) else v for k, v in (entry.get("form") or {}).items()
    }
    app = _app(world)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://app.test",
    ) as client:
        if form:
            reply = await client.request(method, path, data=form, headers=headers)
        else:
            reply = await client.request(method, path, json=body, headers=headers, params=query)
    text = reply.text + json.dumps(dict(reply.headers))
    sent = path + json.dumps(body or {}) + json.dumps(query) + json.dumps(form)
    public = {_value(world, spec) for spec in entry.get("public") or ()}
    # a value the caller sent and the reply echoes back discloses nothing; any other value does
    leaked = sorted(
        v for v in world.canary_values("A") if v in text and v not in sent and v not in public
    )
    assert not leaked, (route, reply.status_code, leaked)
    assert reply.status_code < 500 or entry["probe"] in ("operator", "anonymous"), (
        route,
        reply.status_code,
        reply.text[:300],
    )
    if entry["probe"] == "machine":
        assert reply.status_code == 403, (route, reply.status_code)
    elif entry["probe"] == "anonymous":
        assert "__Host-ai_session" not in reply.headers.get("set-cookie", ""), route
    else:
        assert reply.status_code != 422, (route, "probe body malformed", json.loads(reply.text))
