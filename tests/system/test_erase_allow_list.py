"""The tables a tenant deletion erases are listed twice: in ``config/deletion.yaml`` (the plan the
operator sees) and in the hard-coded allow-list of the database function ``erase_rows`` (what the
database accepts). The newest migration that redefines the function is the live list; a table in
one and not the other either never gets erased or is refused at execution. This ties them."""

from __future__ import annotations

import re
from pathlib import Path

from tenancy import lifecycle

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def _allow_list(sql: str) -> set[str]:
    body = sql[sql.index("IF p_table NOT IN (") :]
    names = body[: body.index(") THEN")]
    return set(re.findall(r"'([a-z_]+)'", names))


def test_the_plan_and_the_database_erase_the_same_tables() -> None:
    redefining = sorted(
        p
        for p in MIGRATIONS.glob("[0-9]*.sql")
        if not p.name.endswith(".down.sql") and "FUNCTION erase_rows" in p.read_text()
    )
    assert redefining, "no migration defines erase_rows"
    live = _allow_list(redefining[-1].read_text())
    assert live == set(lifecycle.load_plan().erase), (
        sorted(live - set(lifecycle.load_plan().erase)),
        sorted(set(lifecycle.load_plan().erase) - live),
    )
