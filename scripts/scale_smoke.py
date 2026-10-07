"""Scale smoke (Phase 3 test harness item 13): SYNTHETIC tenants, accounts and fact rows in a
scratch database, then the client table queries measured under RLS. Report only: it asserts no
latency (none was given); the plan check (index use under RLS) is also a test
(``tests/phase3/test_scale_plan.py``).

Seeds ``--tenants`` tenants with ``--accounts`` accounts each and one finished run per tenant whose
Facts by grade table holds ``--rows`` rows (defaults 50, 100, 4000: 5,000 accounts and 200,000
fact rows). Then, as the runtime role inside each tenant's context: the first page (default
order), ten keyset pages, a sorted page, a filtered page and a search, each timed (median of 5),
and the plan of the first page. Writes Markdown to ``--out``.

Usage: ``ADMIN_DSN=... APP_DSN=... python scripts/scale_smoke.py --out docs/phase3/scale_smoke.md``
(refuses a database whose name does not look like a scratch or test one).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import time
import uuid
from typing import Any

import psycopg

SCALE_PREFIX = "scale-smoke-synthetic"


def seed(admin_dsn: str, tenants: int, accounts: int, rows: int) -> list[tuple[str, str]]:
    """(tenant id, run id) per seeded tenant; COPY for the rows."""
    from views.tables import specs

    cols = [c.key for c in specs()["facts"].columns]
    out: list[tuple[str, str]] = []
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        for t_i in range(tenants):
            tid = c.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{SCALE_PREFIX}-{t_i}",)
            ).fetchone()[0]
            c.execute("INSERT INTO wallets (tenant_id, balance) VALUES (%s, 0)", (tid,))
            with c.cursor().copy("COPY accounts (tenant_id, domain) FROM STDIN") as cp:
                for a in range(accounts):
                    cp.write_row((tid, f"acct-{t_i}-{a}.example"))
            acc = c.execute(
                "SELECT id FROM accounts WHERE tenant_id = %s ORDER BY domain LIMIT 1", (tid,)
            ).fetchone()[0]
            run = c.execute(
                "INSERT INTO account_runs (tenant_id, account_id, status, graph_version,"
                " budget_json, reserved_credits, idempotency_key, budget_usd, finished_at)"
                " VALUES (%s, %s, 'COMPLETED', 'g', '{}', 1, %s, 5, now()) RETURNING id",
                (tid, acc, uuid.uuid4().hex),
            ).fetchone()[0]
            buf = io.StringIO()
            with c.cursor().copy(
                "COPY view_rows (tenant_id, run_id, account_id, table_kind, position, ref, row_id,"
                " cells, search_text, source_state) FROM STDIN"
            ) as cp:
                for p in range(1, rows + 1):
                    cells = {k: f"{k} value {p % 97}" for k in cols}
                    cells["lane"] = ("salesforce", "sap", "data_ai", "other")[p % 4]
                    cp.write_row(
                        (
                            tid,
                            run,
                            acc,
                            "facts",
                            p,
                            f"F-{p}",
                            str(uuid.uuid4()),
                            json.dumps(cells),
                            f"finding {p % 997} lane {cells['lane']}",
                            "no_source",
                        )
                    )
            del buf
            out.append((str(tid), str(run)))
        c.execute("ANALYZE view_rows")
        c.execute("ANALYZE accounts")
    return out


def _timed(c: Any, tenant: str, sql: str, params: dict[str, Any], n: int = 5) -> float:
    times = []
    for _ in range(n):
        with c.transaction():
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            t0 = time.perf_counter()
            c.execute(sql, params).fetchall()
            times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def plan(app_dsn: str, tenant: str, run: str) -> str:
    """EXPLAIN of the first page of the facts table under the tenant's RLS context."""
    from views import query
    from views.tables import specs

    q = query.validate(query.QuerySpec(), specs()["facts"])
    sql, params = query.compile_query(q, run_id=run)
    with psycopg.connect(app_dsn) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        pg_sql, pg_params = _pg(sql, params)
        return "\n".join(r[0] for r in c.execute("EXPLAIN " + pg_sql, pg_params).fetchall())


def _pg(sql: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """SQLAlchemy ``:name`` binds to psycopg ``%(name)s``."""
    import re

    return re.sub(r"(?<!:):([a-z_][a-z0-9_]*)", r"%(\1)s", sql.replace("%", "%%")), params


def measure(app_dsn: str, seeded: list[tuple[str, str]]) -> dict[str, float]:
    """Median milliseconds per query shape over a sample of tenants."""
    from views import query
    from views.tables import specs

    spec = specs()["facts"]
    shapes: dict[str, query.QuerySpec] = {
        "first page": query.QuerySpec(),
        "sorted by lane": query.QuerySpec.model_validate(
            {"sort": [{"column": "lane", "direction": "desc"}]}
        ),
        "search": query.QuerySpec(search="finding 42"),
    }
    out: dict[str, list[float]] = {k: [] for k in (*shapes, "ten keyset pages")}
    from psycopg.rows import namedtuple_row

    with psycopg.connect(app_dsn, autocommit=False, row_factory=namedtuple_row) as c:
        for tenant, run in seeded[:: max(1, len(seeded) // 10)]:
            for name, s in shapes.items():
                sql, params = query.compile_query(query.validate(s, spec), run_id=run)
                out[name].append(_timed(c, tenant, *_pg(sql, params)))
            q = query.validate(query.QuerySpec(), spec)
            cursor = None
            t0 = time.perf_counter()
            for _ in range(10):  # LOOP(scale keyset pages): cap=10 pages; exit=cap or last page
                sql, params = query.compile_query(q, run_id=run, cursor=cursor)
                with c.transaction():
                    c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                    rows = c.execute(*_pg(sql, params)).fetchall()
                cursor = query.next_cursor(rows, q)
                if cursor is None:
                    break
            out["ten keyset pages"].append((time.perf_counter() - t0) * 1000)
    return {k: statistics.median(v) for k, v in out.items() if v}


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--tenants", type=int, default=50)
    ap.add_argument("--accounts", type=int, default=100)
    ap.add_argument("--rows", type=int, default=4000)
    ap.add_argument("--out", default="docs/phase3/scale_smoke.md")
    a = ap.parse_args(argv)
    admin_dsn, app_dsn = os.environ["ADMIN_DSN"], os.environ["APP_DSN"]
    db = admin_dsn.rsplit("/", 1)[-1]
    if not (db.startswith("ai_") or "test" in db or "scratch" in db):
        print("scale_smoke: refused (not a scratch or test database)", file=sys.stderr)
        return 2
    t0 = time.perf_counter()
    seeded = seed(admin_dsn, a.tenants, a.accounts, a.rows)
    seed_s = time.perf_counter() - t0
    timings = measure(app_dsn, seeded)
    explain = plan(app_dsn, *seeded[0])
    lines = [
        "# Scale smoke (report only)",
        "",
        f"Seeded {a.tenants} synthetic tenants, {a.tenants * a.accounts} accounts and "
        f"{a.tenants * a.rows} Facts by grade rows (one finished run per tenant) "
        f"in {seed_s:.1f} s, "
        "on a local PostgreSQL 16 container (laptop; not a production measurement). Queries run as "
        "`app_rw` inside the tenant's RLS context; median of 5 per tenant over a sample of "
        f"{len(seeded[:: max(1, len(seeded) // 10)])} tenants, then the median of those.",
        "",
        "No latency target was given, so none is asserted. The plan check is asserted by "
        "`tests/phase3/test_scale_plan.py`.",
        "",
        "| Query | Median ms |",
        "|---|---|",
        *[f"| {k} | {v:.2f} |" for k, v in timings.items()],
        "",
        "Plan of the first page:",
        "",
        "```",
        explain,
        "```",
        "",
    ]
    with open(a.out, "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
