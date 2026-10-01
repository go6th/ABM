"""Helpers for constraint tests shared by every table's test module."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, text

# column -> (value, SQL type used to cast the parameter)
Row = dict[str, tuple[Any, str]]


def constraint_holds(conn: Connection, name: str, row: Row) -> bool:
    """Evaluate one CHECK constraint, as defined in Postgres, against one row of values.

    Inserting a row only reports the first failing constraint (Postgres checks them in name
    order), so each constraint is tested alone this way. A CHECK passes when its expression is
    true or null.
    """
    definition = conn.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"), {"n": name}
    ).scalar_one()
    expr = definition.removeprefix("CHECK ")
    select_list = ", ".join(f"CAST(:{c} AS {t}) AS {c}" for c, (_, t) in row.items())
    query = f"SELECT {expr} FROM (SELECT {select_list}) AS t"  # noqa: S608
    result = conn.execute(text(query), {c: v for c, (v, _) in row.items()}).scalar_one()
    return result is not False


def check_constraint_names(conn: Connection, qualified_table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"
        ),
        {"t": qualified_table},
    ).scalars()
    return set(rows)
