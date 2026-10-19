#!/usr/bin/env python3
"""Replace data and enrichment tool names with "6th agents" in build sources, then check rendered text.
Usage: python strip_tool_names.py <source files...> --check <rendered text files...>
Edit NAMES for the tools in use. Client facing files must come out clean."""
import re, sys
NAMES = ["BitScale", "PredictLeads", "Sumble", "Firecrawl", "Parallel Search", "Parallel"]
args = sys.argv[1:]
src = args[: args.index("--check")] if "--check" in args else args
chk = args[args.index("--check") + 1 :] if "--check" in args else []
for f in src:
    t = open(f, encoding="utf-8").read(); o = t
    for n in NAMES: t = re.sub(r"\b" + re.escape(n) + r"\b", "6th agents", t)
    t = t.replace("6th agents, 6th agents", "6th agents")
    if t != o: open(f, "w", encoding="utf-8").write(t); print("edited", f)
bad = 0
for f in chk:
    t = open(f, encoding="utf-8", errors="ignore").read()
    for n in NAMES + ["credits"]:
        c = len(re.findall(r"\b" + re.escape(n) + r"\b", t, re.I)); bad += c
        if c: print(f, n, c)
print("PASS" if not bad else f"FAIL: {bad}")
sys.exit(1 if bad else 0)
