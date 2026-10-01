#!/usr/bin/env python3
"""Report tenant scoping and row-level security for the data layer schemas.

The platform skill's scripts/check_rls_catalog.sql only inspects the ``public`` schema and assumes
the roles app_rw and public_reader exist, so against this repo's ``intel`` schemas it passes without
checking anything. This script checks the schemas this repo actually uses.

Rule (platform skill rule 1, conflict C3): every tenant-owned table has ``tenant_id`` NOT NULL and
ROW LEVEL SECURITY enabled and forced. Whether that rule applies now or in roadmap Phase 3 is open
decision R-01 in references/00-reconciliation.md, so by default this only reports.

Usage: DATABASE_URL=postgresql://... python check_tenancy.py [--strict]
       [--schemas intel,intel_internal]
Exit 0 in report mode; with --strict, exit 1 when any table breaks the rule.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

import psycopg

QUERY = """
SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity,
       a.attname IS NOT NULL AS has_tenant_id, COALESCE(a.attnotnull, false) AS tenant_not_null,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p') AND NOT c.relispartition
ORDER BY 1, 2
"""


@dataclass(frozen=True)
class TableState:
    schema: str
    table: str
    rls: bool
    forced: bool
    has_tenant_id: bool
    tenant_not_null: bool
    policies: int

    def problems(self) -> list[str]:
        out = []
        if not self.has_tenant_id:
            out.append("no tenant_id column")
        elif not self.tenant_not_null:
            out.append("tenant_id is nullable")
        if not (self.rls and self.forced):
            out.append("row level security not enabled and forced")
        elif self.policies == 0:
            out.append("RLS on but no policy (all rows hidden)")
        return out


def inspect(url: str, schemas: list[str]) -> list[TableState]:
    with psycopg.connect(url) as conn:
        return [TableState(*row) for row in conn.execute(QUERY, (schemas,)).fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--schemas", default="intel,intel_internal")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    if not args.url:
        print("SKIP: no DATABASE_URL (or --url) given")
        return 0
    states = inspect(args.url, [s.strip() for s in args.schemas.split(",") if s.strip()])
    if not states:
        print("SKIP: no tables in the managed schemas (is the database migrated?)")
        return 0
    bad = 0
    for st in states:
        problems = st.problems()
        bad += bool(problems)
        status = "ok" if not problems else "; ".join(problems)
        print(f"{st.schema}.{st.table}: {status}")
    verdict = "FAILED" if (bad and args.strict) else ("REPORT" if bad else "OK")
    print(f"{verdict}: {bad} of {len(states)} tables not tenant-isolated")
    return 1 if (bad and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
