"""Run the workspace repair stage over an existing export and, optionally, a finished reference
workspace, and write the repaired archive and its report.

Account-neutral: every fact about the workspace is an argument or a data file.

    uv run python scripts/repair/regress.py \
        --name "Acme Holdings, Inc." --domain acme.com --run-date 2026-10-19 \
        --export-zip ~/Downloads/Acme-everything.zip \
        --reference-dir ~/Downloads/Acme/ --entities entities.json --baseline baseline.json \
        --out /tmp/acme-repaired.zip

``--reference-dir`` holds the reference ``*_Job_Postings.xlsx`` and ``*_People_List.xlsx`` (their
posting texts and profiles replace the export's rows, which carry no text); without it the export's
own tables are repaired as they are. ``--entities`` is a JSON list of entity rules (label,
patterns, counts_as_account, before); ``--baseline`` a JSON object of expected numbers named
``<table>.<stat>`` (see ``report.table_numbers``). Reads files, calls no network, spends nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from exports.repair import offline as OF  # noqa: E402
from exports.repair import pipeline as PL  # noqa: E402
from exports.repair.package import build_zip  # noqa: E402
from exports.repair.profile import profile_from_parts  # noqa: E402
from exports.repair.records import (  # noqa: E402
    ArticleRecord,
    FactLine,
    RepairInput,
)
from exports.repair.report import TableBefore, table_numbers  # noqa: E402

KEYS = {
    "jobs": ("Job postings", ("Posting",)),
    "people": ("People", ("Name",)),
    "tech": ("Tech stack and teams", ("Technology",)),
    "news": ("News", ("Source",)),
    "sources": ("Sources", ("Link",)),
    "searches": ("Web searches", ("Searched for",)),
    "signals": ("Signals", ("Signal",)),
    "coverage": ("Coverage", ("Research item",)),
    "mail": ("From your mail and drive", ("Title",)),
}


def articles_of(table: OF.LegacyTable) -> list[ArticleRecord]:
    """The old News rows (extracted fragments dated with the collection day) as articles with no
    page title and no real date, grouped by link."""
    grouped: dict[str, list[FactLine]] = {}
    seen: dict[str, date | None] = {}
    for i, r in enumerate(table.dicts()):
        url = r.get("Source", "")
        grouped.setdefault(url, []).append(
            FactLine(
                fact_id=f"legacy:{i}", text=r.get("Headline", ""), grade=r.get("Fact grade", "")
            )
        )
        seen.setdefault(url, OF.parse_day(r.get("Published", "")))
    from datetime import datetime

    return [
        ArticleRecord(
            url=url,
            collected=datetime(d.year, d.month, d.day) if d else None,
            facts=tuple(facts),
        )
        for url, facts in grouped.items()
        if url
        for d in [seen[url]]
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--run-date", required=True, type=date.fromisoformat)
    ap.add_argument("--export-zip", type=Path)
    ap.add_argument("--reference-dir", type=Path)
    ap.add_argument("--short", default="", help="the name the account's own text uses")
    ap.add_argument("--entities", type=Path)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--people-consent", action="store_true")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    watch: dict[str, object] = {"short_name": args.short}
    if args.entities:
        watch["entities"] = json.loads(args.entities.read_text())
    profile = profile_from_parts(
        name=args.name,
        domain=args.domain,
        run_date=args.run_date,
        watch_config=watch,
        title_exclusions=_exclusions(),
        people_consent=args.people_consent or bool(args.reference_dir),
    )
    inp = RepairInput()
    before: dict[str, TableBefore] = {}
    legacy: dict[str, OF.LegacyTable] = {}
    if args.export_zip:
        legacy, _names = OF.read_export_zip(args.export_zip)
        for key, (stem, cols) in KEYS.items():
            if stem in legacy:
                m = OF.measure(legacy[stem], cols)
                before[key] = TableBefore(m["rows"], m["empty_cells"], m["duplicate_keys"])
        if "Tech stack and teams" in legacy:
            inp.tech = OF.legacy_tech(legacy["Tech stack and teams"])
        if "Sources" in legacy:
            inp.sources = OF.legacy_sources(legacy["Sources"])
        if "Web searches" in legacy:
            inp.searches = OF.legacy_searches(legacy["Web searches"])
        if "Coverage" in legacy:
            inp.coverage = OF.legacy_coverage(legacy["Coverage"])
        if "News" in legacy:
            inp.articles = articles_of(legacy["News"])
        if "Job postings" in legacy:
            inp.jobs = OF.legacy_jobs(legacy["Job postings"], args.run_date)
        if "People" in legacy:
            inp.people = OF.legacy_people(legacy["People"])
    if args.reference_dir:
        jobs_xlsx = next(args.reference_dir.glob("*Job_Postings.xlsx"), None)
        people_xlsx = next(args.reference_dir.glob("*People_List.xlsx"), None)
        if jobs_xlsx:
            inp.jobs = OF.reference_jobs(jobs_xlsx, args.run_date)
        if people_xlsx:
            inp.people = OF.reference_people(people_xlsx)
    baseline = json.loads(args.baseline.read_text()) if args.baseline else None
    result = PL.repair(profile, inp, before=before, baseline=baseline)
    args.out.write_bytes(
        build_zip(
            profile,
            result.tables,
            deliverables={},
            report_md=result.report_md,
            notes=result.notes,
        )
    )
    for c in result.checks:
        print(("PASS " if c.passed else "FAIL ") + c.name + ": " + c.detail[:160])
    nums = table_numbers(result.tables)
    for k in sorted(nums):
        if k.endswith(".rows") or k.startswith("jobs.per_platform.") and k.endswith(".postings"):
            print(f"  {k} = {nums[k]}")
    print("wrote", args.out)
    return 0 if result.passed else 1


def _exclusions() -> tuple[str, ...]:
    from agents.config import load_skill_config

    return tuple(load_skill_config().scope.excluded_title_patterns)


if __name__ == "__main__":
    raise SystemExit(main())
