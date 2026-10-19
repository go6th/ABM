#!/usr/bin/env python3
"""Gate 9 and 9a: scan Office files for data tool names and list every external domain.

Usage: python third_party_scan.py file1.docx file2.xlsx ...
Scans the unzipped package (all XML and rels), not the rendered text.
Exit 1 if any tool name is found. Prints every domain found for the 9a sort.
"""
import re, sys, zipfile, collections

TOOLS = re.compile(r"sumble|bitscale|predictleads|predict\s*leads|firecrawl|parallel\.ai|\bparallel search|apollo\.io|clay\.com|grain\.com", re.I)
WARN = re.compile(r"\bcredits?\b", re.I)  # posting text legitimately says credit notes, carbon credits; a reviewer reads these
DOMAIN = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})", re.I)

def scan(path):
    z = zipfile.ZipFile(path)
    hits = collections.Counter(); domains = collections.Counter(); warns = collections.Counter()
    for name in z.namelist():
        if not (name.endswith(".xml") or name.endswith(".rels")):
            continue
        text = z.read(name).decode("utf-8", "replace")
        for m in TOOLS.finditer(text):
            hits[(name, m.group().lower())] += 1
        for m in WARN.finditer(text):
            warns[name] += 1
        for m in DOMAIN.finditer(text):
            d = m.group(1).lower()
            if d.startswith("www."): d = d[4:]
            if "openxmlformats" in d or "microsoft.com" in d and "schemas" in d or d.startswith("schemas."): continue
            domains[d] += 1
    return hits, domains, warns

bad = 0
for p in sys.argv[1:]:
    hits, domains, warns = scan(p)
    print(f"== {p}")
    if hits:
        bad += 1
        for (part, word), n in sorted(hits.items()):
            print(f"  TOOL NAME: {word!r} x{n} in {part}")
    else:
        print("  tool names: none")
    for part, n in warns.items():
        print(f"  WARN: 'credit' x{n} in {part}; read them, block only if they are about tool credits")
    print("  domains:")
    for d, n in sorted(domains.items(), key=lambda x: -x[1]):
        print(f"    {n:4d}  {d}")
sys.exit(1 if bad else 0)
