#!/usr/bin/env python3
"""Migration lint for tenant isolation (Architecture v2 section 4.2). Exit 1 on any violation.

Usage: python scripts/lint_migrations.py path/to/migrations [more paths]
Scans .sql files. Rules:
  R1 every CREATE TABLE that has a tenant_id column needs ENABLE and FORCE ROW LEVEL SECURITY
  R2 ...and at least one CREATE POLICY on it that has both USING and WITH CHECK
  R3 no BYPASSRLS granted: CREATE/ALTER ROLE ... BYPASSRLS (NOBYPASSRLS is fine)
  R4 SECURITY DEFINER functions must carry a '-- reviewed:' comment on the line above
  R5 if any policy on a table is scoped TO public_reader, every other policy on that table must also name
     a role (TO app_rw) or be AS RESTRICTIVE. Permissive policies are OR-ed, and a policy with no TO clause
     applies to every role, so an unscoped tenant policy lets public_reader read private rows
     (reproduced on PostgreSQL 16; see references/03-tenancy-isolation-security.md)
Limits: regex based; does not parse SQL. Tables created or secured inside DO $$ loops are not seen; mark such
a block with '-- lint:covers t1, t2' and rely on scripts/check_rls_catalog.sql (queries the migrated database)
and the isolation suite as the real backstops. All files given are linted together. Library-owned tables (e.g. LangGraph checkpoints) are covered by a
prefix policy applied in a loop; mark that block with '-- lint:covers checkpoints, checkpoint_blobs'.
"""
import re
import sys
from pathlib import Path

CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\.\"]+)\s*\((.*?)\)\s*(?:PARTITION\s+BY[^;]*)?;", re.I | re.S)


def lint_definers(text: str, name: str) -> list[str]:
    errs = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r"SECURITY\s+DEFINER", line, re.I):
            window = "\n".join(lines[max(0, i - 6): i + 1])
            if "-- reviewed:" not in window:
                errs.append(f"{name}:{i + 1}: SECURITY DEFINER without '-- reviewed:' comment")
    return errs


def lint(text: str, name: str) -> list[str]:
    """Table, policy and role rules (R1, R2, R3, R5) over text. Pass ALL migration files joined, because a
    table created in 0001 normally gets its policies in 0002."""
    errs = []
    covered = set()
    for m in re.finditer(r"--\s*lint:covers\s+([\w, ]+)", text):
        covered |= {t.strip().lower() for t in m.group(1).split(",") if t.strip()}
    for m in CREATE.finditer(text):
        table = m.group(1).strip('"').split(".")[-1].lower()
        body = m.group(2)
        if not re.search(r"\btenant_id\b", body, re.I) or table in covered:
            continue
        t = re.escape(table)
        if not re.search(rf"ALTER\s+TABLE\s+{t}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", text, re.I):
            errs.append(f"{name}: {table}: missing ENABLE ROW LEVEL SECURITY")
        if not re.search(rf"ALTER\s+TABLE\s+{t}\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", text, re.I):
            errs.append(f"{name}: {table}: missing FORCE ROW LEVEL SECURITY")
        pols = re.findall(rf"CREATE\s+POLICY\s+\w+\s+ON\s+{t}\b(.*?);", text, re.I | re.S)
        if not pols:
            errs.append(f"{name}: {table}: no CREATE POLICY")
        elif not any(re.search(r"\bUSING\b", p, re.I) and re.search(r"WITH\s+CHECK", p, re.I) for p in pols):
            errs.append(f"{name}: {table}: no policy with both USING and WITH CHECK")
        if any(re.search(r"\bTO\s+public_reader\b", p, re.I) for p in pols):
            for p in pols:
                head = re.split(r"\bUSING\b|WITH\s+CHECK", p, maxsplit=1, flags=re.I)[0]
                if not re.search(r"\bTO\s+\w+", head, re.I) and not re.search(r"\bAS\s+RESTRICTIVE\b", head, re.I):
                    errs.append(f"{name}: {table}: policy without a TO clause on a table that has a public_reader policy "
                                f"(permissive policies are OR-ed: private rows leak to public_reader)")
    for m in re.finditer(r"(?:CREATE|ALTER)\s+ROLE\s+\w+[^;]*?(?<!NO)BYPASSRLS", text, re.I | re.S):
        errs.append(f"{name}: role granted BYPASSRLS: {m.group(0)[:60]!r}")
    return errs


def main(paths: list[str]) -> int:
    files = []
    for p in paths:
        pp = Path(p)
        files += sorted(pp.rglob("*.sql")) if pp.is_dir() else [pp]
    if not files:
        print("no .sql files found")
        return 1
    errs = []
    texts = [(f, f.read_text()) for f in files]
    for f, t in texts:
        errs += lint_definers(t, str(f))
    errs += lint("\n".join(t for _, t in texts), f"{len(files)} file(s)")
    if errs:
        print("\n".join("FAIL " + e for e in errs))
        return 1
    print(f"OK: {len(files)} file(s) pass the isolation migration lint")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["migrations"]))
