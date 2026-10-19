#!/usr/bin/env python3
"""Text checks on a rendered document before delivery.

Checks
  1. Orphan citations: every source ID cited in the text (S12, J4, U2) must exist in the ID list.
  2. Banned dashes: em dash, en dash, and a hyphen used as punctuation (space hyphen space).
  3. Stale phrases: strings that should no longer appear, such as a corrected claim.
  4. Excluded names: people who must not appear, such as security staff the user excluded.

Usage
    pdftotext -layout Account_Summary.pdf summary.txt
    python doc_checks.py summary.txt --ids ids.json
        [--stale "phrase one" "phrase two"] [--exclude "Name One" "Name Two"]

ids.json is a JSON list of valid IDs, for example the keys of the account's URL map:
    node -e "const {U}=require('./urls');console.log(JSON.stringify(Object.keys(U)))" > ids.json
Exit code is 1 if anything is found.
"""

import argparse
import json
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("textfile")
    ap.add_argument("--ids", required=True)
    ap.add_argument("--stale", nargs="*", default=[])
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--verbatim-quotes")
    a = ap.parse_args()

    with open(a.textfile, encoding="utf-8") as text_file:
        text = text_file.read()
    with open(a.ids, encoding="utf-8") as ids_file:
        ids = set(json.load(ids_file))
    if a.verbatim_quotes:
        with open(a.verbatim_quotes, encoding="utf-8") as quotes_file:
            quotes = json.load(quotes_file)
    else:
        quotes = []
    problems = 0

    cited = set(re.findall(r"\b([SJU]\d{1,3})\b", text))
    orphans = sorted(cited - ids, key=lambda s: (s[0], int(s[1:])))
    print(f"citations: {len(cited)} cited, {len(orphans)} orphan {orphans if orphans else ''}")
    problems += len(orphans)

    authored_text = text
    for quote in quotes:
        if quote:
            authored_text = authored_text.replace(quote, "")
    dash_hits = re.findall(r".{0,30}(?: - | – |—|–).{0,30}", authored_text)
    print(f"banned dashes: {len(dash_hits)}")
    for h in dash_hits[:10]:
        print("   ", h.strip())
    problems += len(dash_hits)

    for label, items in (("stale phrase", a.stale), ("excluded name", a.exclude)):
        for item in items:
            n = text.count(item)
            if n:
                print(f"{label} still present ({n}): {item}")
                problems += n

    print("PASS" if not problems else f"FAIL: {problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
