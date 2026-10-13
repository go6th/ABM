#!/usr/bin/env python3
"""Research cache savings report: what the cache saved in provider calls and model tokens.

Reads the ledger (``tool_calls``) and the model reply log (``model_reply_log``) through an admin
connection and prints, per tenant: calls served from the cache and the dollars the same calls cost
when they were first made; model replies served from the cache and the tokens and dollars of the
replies they reused. Figures are per tenant id only (no tenant name, no account name).

Usage: python scripts/token_savings.py [--dsn postgresql://...] [--since 2026-10-01]
(``--dsn`` defaults to ``$ADMIN_DSN``).
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

PROVIDER_SAVINGS = """
SELECT c.tenant_id, count(*) AS cached_calls,
       coalesce(sum(o.cost_usd), 0) AS saved_usd
FROM tool_calls c
JOIN LATERAL (
    SELECT cost_usd FROM tool_calls o
    WHERE o.tenant_id = c.tenant_id AND o.args_hash = c.args_hash
      AND o.cost_source IS DISTINCT FROM 'cache' AND o.created_at <= c.created_at
    ORDER BY o.created_at DESC LIMIT 1
) o ON true
WHERE c.cost_source = 'cache' AND c.created_at >= %(since)s
GROUP BY c.tenant_id ORDER BY saved_usd DESC
"""

MODEL_SAVINGS = """
SELECT h.tenant_id, count(*) AS cached_replies,
       coalesce(sum(m.input_tokens + m.output_tokens), 0) AS saved_tokens,
       coalesce(sum(m.cost_usd), 0) AS saved_usd
FROM model_reply_log h
JOIN LATERAL (
    SELECT input_tokens, output_tokens, cost_usd FROM model_reply_log m
    WHERE m.tenant_id = h.tenant_id AND m.cache_key = h.cache_key AND m.served_from = 'model'
      AND m.created_at <= h.created_at
    ORDER BY m.created_at DESC LIMIT 1
) m ON true
WHERE h.served_from = 'cache' AND h.created_at >= %(since)s
GROUP BY h.tenant_id ORDER BY saved_usd DESC
"""

MODEL_SPEND = """
SELECT tenant_id, task_kind, model, count(*) AS calls,
       sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
       sum(cache_read_tokens) AS cache_read_tokens,
       sum(cost_usd) FILTER (WHERE cost_known) AS cost_usd,
       count(*) FILTER (WHERE NOT cost_known) AS unpriced
FROM model_reply_log WHERE served_from = 'model' AND created_at >= %(since)s
GROUP BY tenant_id, task_kind, model ORDER BY cost_usd DESC NULLS LAST
"""


def main() -> int:
    """Print the three reports."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.environ.get("ADMIN_DSN"))
    parser.add_argument("--since", default="1970-01-01")
    args = parser.parse_args()
    if not args.dsn:
        print("set --dsn or ADMIN_DSN", file=sys.stderr)
        return 2
    with psycopg.connect(args.dsn) as conn:
        params = {"since": args.since}
        print("provider calls served from the cache (dollars the original calls cost):")
        for tenant, calls, usd in conn.execute(PROVIDER_SAVINGS, params):
            print(f"  tenant {tenant}: {calls} calls, {usd:.3f} USD saved")
        print("model replies served from the cache (tokens and dollars of the reused replies):")
        for tenant, replies, tokens, usd in conn.execute(MODEL_SAVINGS, params):
            print(f"  tenant {tenant}: {replies} replies, {tokens} tokens, {usd:.3f} USD saved")
        print("model spend by task kind and model (calls that went to the provider):")
        for row in conn.execute(MODEL_SPEND, params):
            tenant, task, model, calls, i, o, cr, usd, unpriced = row
            cost = f"{usd:.3f} USD" if usd is not None else "unpriced"
            print(
                f"  tenant {tenant}: {task} on {model}: {calls} calls, {i} in, {o} out,"
                f" {cr} cached reads, {cost}" + (f" ({unpriced} unpriced)" if unpriced else "")
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
