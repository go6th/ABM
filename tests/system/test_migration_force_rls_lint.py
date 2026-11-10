"""SD-18: a data change in a migration must not silently touch zero rows.

Every tenant table FORCEs row level security with policies for the runtime roles only, so the
migrating owner sees no row: an ``UPDATE`` or ``DELETE`` in a migration "succeeds" and changes
nothing (the 2026-11-08 staging migrate of 0115 failed on exactly this). A migration that changes
rows of a FORCE table must lift it first (``ALTER TABLE t NO FORCE ROW LEVEL SECURITY``) and put it
back in the same file, as 0115 does. ``INSERT`` is not checked: without a policy it fails loudly.

The migrations written before this rule are listed in ``BEFORE_THE_RULE`` (ratchet: shrink, never
grow); whether their changes reached the live database is checked separately (SD-18 live checks in
docs/system-design/07-fix-log.md). Function bodies (``$$ ... $$``) are skipped: they run later,
under their own role.

GUARANTEE(test_no_new_migration_changes_rows_of_a_force_rls_table_unlifted)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

# ratchet: shrink, never grow. The audit listed 16 (2026-11-10); 12 of them change rows only
# inside function bodies, which run later under their own role, so 4 remain. Remove one once its
# change is re-checked on the live database and found applied, or re-done in a new migration.
BEFORE_THE_RULE = {"0043", "0063", "0079", "0112"}

_DOLLAR = re.compile(r"\$(\w*)\$.*?\$\1\$", re.S)
_COMMENT = re.compile(r"--[^\n]*")
_DML = re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?([a-z_][a-z0-9_]*)", re.I)
_FORCE = re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?([a-z_][a-z0-9_]*)\s+FORCE\s+ROW\s+LEVEL", re.I)
_NO_FORCE = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?([a-z_][a-z0-9_]*)\s+NO\s+FORCE\s+ROW\s+LEVEL", re.I
)


def _ups() -> list[Path]:
    return sorted(p for p in MIGRATIONS.glob("[0-9]*.sql") if not p.name.endswith(".down.sql"))


def _body(path: Path) -> str:
    return _DOLLAR.sub(" ", _COMMENT.sub(" ", path.read_text()))


def force_tables() -> set[str]:
    """Every table that FORCEs row level security in the migrated test database (some are set in
    a loop by 0002, which a text scan cannot see), plus any a migration FORCEs by name."""
    import psycopg

    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN) as c:
        out = {
            str(r[0])
            for r in c.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'public' AND c.relforcerowsecurity"
            )
        }
    for path in _ups():
        out |= {m.group(1).lower() for m in _FORCE.finditer(_body(path))}
    return out


def unlifted(path: Path, forced: set[str]) -> list[str]:
    """Tables whose rows this migration changes while FORCE is on (not lifted before)."""
    body = _body(path)
    out = []
    for m in _DML.finditer(body):
        table = m.group(2).lower()
        if table not in forced:
            continue
        before = body[: m.start()]
        lifts = [x.start() for x in _NO_FORCE.finditer(before) if x.group(1).lower() == table]
        puts = [x.start() for x in _FORCE.finditer(before) if x.group(1).lower() == table]
        if not lifts or (puts and max(puts) > max(lifts)):
            out.append(table)
    return sorted(set(out))


@pytest.mark.requires_postgres
def test_no_new_migration_changes_rows_of_a_force_rls_table_unlifted() -> None:
    forced = force_tables()
    assert {"facts", "documents", "research_jobs"} <= forced  # the scan sees the FORCE tables
    found = {p.name[:4]: unlifted(p, forced) for p in _ups()}
    offending = {rev: tables for rev, tables in found.items() if tables}
    new = {rev: t for rev, t in offending.items() if rev not in BEFORE_THE_RULE}
    assert not new, f"lift FORCE around these row changes (see 0115): {new}"
    stale = sorted(BEFORE_THE_RULE - set(offending))
    assert not stale, f"no longer offending, remove from BEFORE_THE_RULE: {stale}"


def test_the_lint_catches_an_unlifted_update_and_passes_a_lifted_one(tmp_path: Path) -> None:
    forced = {"documents"}
    bad = tmp_path / "9998_bad.sql"
    bad.write_text("UPDATE documents SET audience = 'client' WHERE audience = 'x';\n")
    good = tmp_path / "9999_good.sql"
    good.write_text(
        "ALTER TABLE documents NO FORCE ROW LEVEL SECURITY;\n"
        "UPDATE documents SET audience = 'client';\n"
        "ALTER TABLE documents FORCE ROW LEVEL SECURITY;\n"
        "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$ DELETE FROM documents $$;\n"
    )
    assert unlifted(bad, forced) == ["documents"]
    assert unlifted(good, forced) == []
