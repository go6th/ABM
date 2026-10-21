#!/usr/bin/env python3
"""Prompt-cache report from Cloud Logging: the cache-read share of input tokens per surface.

Reads two structured log events (no prompt text, token counts only): ``llm.usage`` (one line per
research call, written by ``platform.llm.AnthropicLlm`` since 2026-10-21, with ``kind`` saying
how the call met the cache: warm, follow, lead or none) and ``ask.route`` (one line per Ask
answer). Prints, per surface, tier and model: calls, uncached input, cache reads, cache writes,
and the read share = reads / (reads + writes + uncached).

Usage: python scripts/cache_report.py [--project abmgenerator] [--freshness 7d] [--limit 5000]
(needs ``gcloud`` signed in with logging read access).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict

FILTER = (
    'jsonPayload.event="llm.usage"'
    ' OR (jsonPayload.event="ask.route" AND jsonPayload.outcome="model")'
)


def share(read: int, written: int, uncached: int) -> str:
    """The cache-read share of all input tokens, as a percentage."""
    total = read + written + uncached
    return f"{100 * read / total:5.1f}%" if total else "    -"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="abmgenerator")
    parser.add_argument("--freshness", default="7d")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    out = subprocess.run(
        [
            "gcloud", "logging", "read", FILTER, f"--project={args.project}",
            f"--freshness={args.freshness}", f"--limit={args.limit}", "--format=json(jsonPayload)",
        ],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    rows: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0, 0])
    kinds: dict[str, int] = defaultdict(int)
    for entry in json.loads(out or "[]"):
        p = entry.get("jsonPayload") or {}
        surface = "research" if p.get("event") == "llm.usage" else "ask"
        tier = str(p.get("tier") or p.get("route") or "-")
        model = str(p.get("model") or p.get("model_id") or "-")
        row = rows[(surface, tier, model)]
        row[0] += 1
        row[1] += int(p.get("input_tokens") or 0)
        row[2] += int(p.get("cache_read_tokens") or 0)
        row[3] += int(p.get("cache_write_tokens") or 0)
        row[4] += int(p.get("output_tokens") or 0)
        if surface == "research":
            kinds[str(p.get("kind") or "-")] += 1
    if not rows:
        print("no llm.usage or ask.route lines in the window")
        return 0
    head = f"{'surface':9} {'tier':12} {'model':20} {'calls':>6} {'uncached':>10} {'read':>11}"
    print(f"{head} {'written':>10} {'output':>9}  read share")
    totals = [0, 0, 0, 0, 0]
    for (surface, tier, model), r in sorted(rows.items()):
        totals = [a + b for a, b in zip(totals, r, strict=True)]
        print(
            f"{surface:9} {tier[:12]:12} {model[:20]:20} {r[0]:6} {r[1]:10} {r[2]:11} {r[3]:10}"
            f" {r[4]:9}  {share(r[2], r[3], r[1])}"
        )
    print(f"{'all':43} {totals[0]:6} {totals[1]:10} {totals[2]:11} {totals[3]:10} {totals[4]:9}"
          f"  {share(totals[2], totals[3], totals[1])}")  # fmt: skip
    if kinds:
        print("research calls by cache gate role:", dict(sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
