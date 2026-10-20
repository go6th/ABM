#!/usr/bin/env python3
"""Model spend report: where the Claude API money goes, from the ``llm_spend_daily`` view.

Reads the view (migration 0079) through an admin connection and prints, per day: spend by
surface (Ask, chat, research run), then the largest lines by surface, task kind and model with
their token classes, the cache-read share and whether they were batched. Figures are per tenant
id only (no tenant name, no account name). Token review item 19 (2026-10-20): without this view
nobody can tell which change saves the most.

Usage: python scripts/cost_report.py [--dsn postgresql://...] [--since 2026-10-01] [--top 15]
(``--dsn`` defaults to ``$ADMIN_DSN``).
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

BY_SURFACE = """
SELECT day, surface, sum(calls) AS calls, sum(cost_usd) AS cost_usd
FROM llm_spend_daily WHERE day >= %(since)s
GROUP BY day, surface ORDER BY day DESC, cost_usd DESC
"""

TOP_LINES = """
SELECT tenant_id, surface, task_kind, model, batch, sum(calls) AS calls,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       sum(cache_read_tokens) AS cache_read_tokens, sum(cache_write_tokens) AS cache_write_tokens,
       sum(cost_usd) AS cost_usd
FROM llm_spend_daily WHERE day >= %(since)s
GROUP BY tenant_id, surface, task_kind, model, batch
ORDER BY cost_usd DESC NULLS LAST LIMIT %(top)s
"""


def share(read: int, uncached: int, written: int) -> str:
    """The cache-read share of all input tokens, as a percentage."""
    total = read + uncached + written
    return f"{100 * read / total:.0f}%" if total else "n/a"


def main() -> int:
    """Print the daily totals and the largest lines."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("ADMIN_DSN"))
    parser.add_argument("--since", default="1970-01-01")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()
    if not args.dsn:
        print("set --dsn or ADMIN_DSN", file=sys.stderr)
        return 2
    with psycopg.connect(args.dsn) as conn:
        params = {"since": args.since, "top": args.top}
        print("model spend per day by surface:")
        for day, surface, calls, usd in conn.execute(BY_SURFACE, params):
            print(f"  {day} {surface}: {calls} calls, {usd:.3f} USD")
        print(f"largest {args.top} lines (surface, task kind, model):")
        for row in conn.execute(TOP_LINES, params):
            tenant, surface, task, model, batch, calls, i, o, cr, cw, usd = row
            print(
                f"  tenant {tenant}: {surface}/{task} on {model}{' (batch)' if batch else ''}:"
                f" {calls} calls, {i} in, {o} out, {cr} cache reads, {cw} cache writes,"
                f" cache-read share {share(cr, i, cw)}, {usd:.3f} USD"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
