"""Catalog-driven canary planting and cross-tenant probes for every tenant table (TN1, TN3).

SYNTHETIC data only. A canary row is synthesised for a table from the database catalog: every
NOT NULL column without a default gets a value of its type, single-column ``= ANY (ARRAY[..])``
checks pick their first allowed value, and the matrix file (``matrix.yaml``) overrides what the
catalog cannot express. Rows are planted by the migrator (a superuser in the test cluster) with
``session_replication_role = replica`` so foreign keys and triggers do not demand a whole object
graph: the probes test row-level security on THIS table, and the planted row only has to exist.

Probes run as ``app_rw`` (the runtime role, NOBYPASSRLS, not the owner) inside tenant B's
transaction-local context, exactly as ``tenant_txn`` sets it. Every probe has a closed set of
acceptable outcomes; anything else is a failure, never a skip.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

CANARY_PREFIX = "CANARY-TN1-"
REFUSED = ("row-level security", "permission denied")


@dataclass(frozen=True)
class Column:
    """One column as the catalog describes it."""

    name: str
    type: str
    not_null: bool
    has_default: bool
    generated: bool
    identity: bool


@dataclass(frozen=True)
class Table:
    """A tenant table: its columns, primary key and single-column value lists from checks."""

    name: str
    kind: str  # 'r' or 'p'
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    enums: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    key_column: str = "tenant_id"


_ANY = re.compile(r"\(+\s*\(?(\w+)\)?(?:::\w+)?\s*=\s*ANY\s*\(+ARRAY\[(.*?)\]", re.S)
_IN = re.compile(r"\(+\s*(\w+)\s+IN\s*\((.*?)\)", re.S)
_LIT = re.compile(r"'((?:[^']|'')*)'")
_HEX64 = re.compile(r"\(?(\w+) ~ '\^\[0-9a-f\]\{64\}\$'")
_ONLY_EQ = re.compile(r"^CHECK \(\((\w+) = '((?:[^']|'')*)'::text\)\)$")
_VEC = re.compile(r"^(?:half)?vec(?:tor)?\((\d+)\)$")


def tenant_tables(conn: psycopg.Connection[Any]) -> list[Table]:
    """Every public table (not a partition child) with a ``tenant_id`` or ``thread_id`` column."""
    rows = conn.execute(
        """
        SELECT c.oid, c.relname, c.relkind
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
           AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                        AND a.attname IN ('tenant_id', 'thread_id') AND NOT a.attisdropped)
         ORDER BY c.relname
        """
    ).fetchall()
    out: list[Table] = []
    for oid, name, kind in rows:
        cols = tuple(
            Column(n, t, nn, d, g, i)
            for n, t, nn, d, g, i in conn.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                       a.atthasdef, a.attgenerated <> '', a.attidentity <> ''
                  FROM pg_attribute a
                 WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                 ORDER BY a.attnum
                """,
                (oid,),
            ).fetchall()
        )
        pk = tuple(
            r[0]
            for r in conn.execute(
                """
                SELECT a.attname FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = %s AND i.indisprimary
                 ORDER BY array_position(i.indkey, a.attnum)
                """,
                (oid,),
            ).fetchall()
        )
        enums: dict[str, tuple[str, ...]] = {}
        for (definition,) in conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s"
            " AND contype = 'c'",
            (oid,),
        ).fetchall():
            for m in _HEX64.finditer(definition):
                enums.setdefault(m.group(1), ("{hex64}",))
            eq = _ONLY_EQ.match(definition)
            if eq:
                enums.setdefault(eq.group(1), (eq.group(2).replace("''", "'"),))
            for pattern in (_ANY, _IN):
                for m in pattern.finditer(definition):
                    values = tuple(v.replace("''", "'") for v in _LIT.findall(m.group(2)))
                    if values and m.group(1) not in enums:
                        enums[m.group(1)] = values
        names = {c.name for c in cols}
        out.append(
            Table(name, kind, cols, pk, enums, "tenant_id" if "tenant_id" in names else "thread_id")
        )
    return out


def _value(table: Table, col: Column, tenant: UUID, canary: str) -> Any:
    """A synthetic value of the column's type (overrides are applied by the caller)."""
    if col.name == "tenant_id":
        return tenant
    if col.name == "thread_id":
        return uuid4() if col.type == "uuid" else f"{tenant}:{uuid4()}"
    if col.name in table.enums:
        return _render(table.enums[col.name][0], tenant, canary)
    t = col.type
    if t.endswith("[]"):
        return []
    if t == "character(64)":
        return secrets.token_hex(32)
    vec = _VEC.match(t)
    if vec:
        return "[" + ",".join(["0"] * int(vec.group(1))) + "]"
    if t == "uuid":
        return uuid4()
    if t.startswith(("text", "character varying", "citext")):
        return f"{canary}-{col.name}"
    if t in ("integer", "bigint", "smallint") or t.startswith("numeric"):
        return 1
    if t in ("double precision", "real"):
        return 1.0
    if t == "boolean":
        return False
    if t in ("jsonb", "json"):
        return psycopg.types.json.Jsonb({"canary": canary})
    if t.startswith("timestamp"):
        return "2026-01-01T00:00:00+00:00"
    if t == "date":
        return "2026-01-01"
    if t == "bytea":
        return canary.encode()
    if t == "interval":
        return "1 hour"
    if t == "inet":
        return "192.0.2.1"
    if t == "tsvector":
        return canary
    raise ValueError(f"{table.name}.{col.name}: no synthetic value for type {t}")


def _render(value: Any, tenant: UUID, canary: str) -> Any:
    """Expand override placeholders: ``{tenant}``, ``{uuid}``, ``{canary}``, ``{hex64}``."""
    if not isinstance(value, str):
        return value
    out = (
        value.replace("{tenant}", str(tenant))
        .replace("{canary}", canary)
        .replace("{hex64}", secrets.token_hex(32))
    )
    while "{uuid}" in out:
        out = out.replace("{uuid}", str(uuid4()), 1)
    return out


def synthesise(
    table: Table, tenant: UUID, overrides: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Column values for one canary row of ``table`` owned by ``tenant``."""
    canary = f"{CANARY_PREFIX}{tenant.hex[:8]}-{secrets.token_hex(3)}"
    values: dict[str, Any] = {}
    over = dict(overrides or {})
    for col in table.columns:
        if col.generated or (col.identity and col.name not in over):
            continue
        if col.name in over:
            values[col.name] = _render(over[col.name], tenant, canary)
        elif col.name in (table.key_column,) or (col.not_null and not col.has_default):
            values[col.name] = _value(table, col, tenant, canary)
    if any(c.name == "owner_user_id" for c in table.columns):
        # private workspaces (0063): every owned row names its user; the default (the acting
        # user) and the fill trigger are off for the migrator's plant
        values.setdefault("owner_user_id", f"matrix-owner-{tenant.hex[:8]}")
    values.setdefault(table.key_column, tenant if table.key_column == "tenant_id" else None)
    if values[table.key_column] is None:
        values[table.key_column] = f"{tenant}:{uuid4()}"
    return values


def plant(
    admin: psycopg.Connection[Any],
    table: Table,
    tenant: UUID,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one canary row as the migrator with triggers and foreign keys off; returns every
    planted value (the primary key columns as the database assigned them)."""
    values = synthesise(table, tenant, overrides)
    cols = list(values)
    key = list(table.primary_key) or cols
    with admin.transaction():
        admin.execute("SET LOCAL session_replication_role = replica")
        row = admin.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
                sql.Identifier(table.name),
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                sql.SQL(", ").join(map(sql.Identifier, key)),
            ),
            [values[c] for c in cols],
        ).fetchone()
    assert row is not None
    return {**values, **dict(zip(key, row, strict=True))}


def _where(key: Mapping[str, Any]) -> sql.Composed:
    return sql.SQL(" AND ").join(
        sql.SQL("{} IS NOT DISTINCT FROM {}").format(sql.Identifier(k), sql.Literal(v))
        for k, v in key.items()
    )


@dataclass(frozen=True)
class Outcome:
    """One probe's result: ``ok`` when the database refused or hid the row."""

    op: str
    ok: bool
    detail: str


def _as_tenant(
    app_dsn: str,
    tenant: UUID | None,
    statement: sql.Composable,
    *,
    user: str | None = None,
    scope: str = "system",
) -> tuple[int, str]:
    """Run one statement as app_rw in ``tenant``'s transaction-local context (None: no context),
    as ``user`` in ``scope``. The cross-tenant probes run in the system scope, which sees every
    row of its own tenant: the strongest attacker inside a client. Returns (rowcount or
    count(*), error text or '')."""
    try:
        with psycopg.connect(app_dsn) as c, c.transaction():
            if tenant is not None:
                c.execute(
                    "SELECT set_config('app.tenant_id', %s, true),"
                    " set_config('app.user_id', %s, true), set_config('app.scope', %s, true)",
                    (str(tenant), user or "", scope),
                )
            cur = c.execute(statement)
            if cur.description:
                row = cur.fetchone()
                return (int(row[0]) if row else 0), ""
            return cur.rowcount, ""
    except psycopg.Error as e:
        return -1, str(e).splitlines()[0]


def probe(
    app_dsn: str,
    table: Table,
    owner_key: Mapping[str, Any],
    owner: UUID,
    attacker: UUID,
    attacker_key: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
) -> list[Outcome]:
    """Every cross-tenant operation of ``attacker`` against ``owner``'s planted row."""
    t = sql.Identifier(table.name)
    k = sql.Identifier(table.key_column)
    owner_value = str(owner) if table.key_column == "tenant_id" else None
    owned = (
        sql.SQL("{} = {}").format(k, sql.Literal(owner_value))
        if owner_value
        else sql.SQL("starts_with({}, {})").format(k, sql.Literal(f"{owner}:"))
    )
    out: list[Outcome] = []

    def hidden(op: str, statement: sql.Composable, tenant: UUID | None) -> None:
        n, err = _as_tenant(app_dsn, tenant, statement)
        refused = bool(err) and any(r in err for r in REFUSED)
        out.append(Outcome(op, n == 0 or refused, err or f"rows={n}"))

    hidden(
        "read", sql.SQL("SELECT count(*) FROM {} WHERE {}").format(t, _where(owner_key)), attacker
    )
    hidden("list", sql.SQL("SELECT count(*) FROM {} WHERE {}").format(t, owned), attacker)
    hidden("unset_context", sql.SQL("SELECT count(*) FROM {}").format(t), None)
    first = next((c for c in table.columns if not c.generated), None)
    assert first is not None
    col = sql.Identifier(first.name)
    hidden(
        "update",
        sql.SQL("UPDATE {} SET {} = {} WHERE {}").format(t, col, col, _where(owner_key)),
        attacker,
    )
    hidden("delete", sql.SQL("DELETE FROM {} WHERE {}").format(t, _where(owner_key)), attacker)
    # write: a fresh row stamped with the owner's tenant must be refused by WITH CHECK or grants
    values = synthesise(table, owner, overrides)
    cols = list(values)
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        t,
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Literal(_adapt(values[c])) for c in cols),
    )
    n, err = _as_tenant(app_dsn, attacker, insert)
    out.append(Outcome("write", bool(err) and any(r in err for r in REFUSED), err or f"rows={n}"))
    # move: the attacker's own row re-stamped with the owner's tenant must be refused
    if table.key_column == "tenant_id":
        move = sql.SQL("UPDATE {} SET tenant_id = {} WHERE {}").format(
            t, sql.Literal(str(owner)), _where(attacker_key)
        )
        n, err = _as_tenant(app_dsn, attacker, move)
        ok = bool(err) or n == 0
        if n == 0 and not err:
            # zero rows is only acceptable when the attacker could not see its own row either
            seen, _ = _as_tenant(
                app_dsn,
                attacker,
                sql.SQL("SELECT count(*) FROM {} WHERE {}").format(t, _where(attacker_key)),
            )
            ok = seen == 0
        out.append(Outcome("move", ok, err or f"rows={n}"))
    return out


def _adapt(value: Any) -> Any:
    if isinstance(value, psycopg.types.json.Jsonb):
        import json

        return json.dumps(value.obj)
    if isinstance(value, UUID):
        return str(value)
    return value
