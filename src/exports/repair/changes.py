"""The pipeline changes of the repair stage, as the report lists them."""

# ruff: noqa: E501

from __future__ import annotations

# (file, function, what, why)
CHANGES: tuple[tuple[str, str, str, str], ...] = (
    ("src/exports/repair/jobs.py", "build_jobs", "one row per posting ID, platform-only main sheet, entity and class per posting, Open/Closed only", "the old table listed every role, counted divested postings and showed platform names as titles"),
    ("src/exports/repair/entity.py", "tag_entity", "entity from the workspace's entity rules, wording and host", "a divested or look-alike employer was counted as the account"),
    ("src/exports/repair/tech.py", "build_tech", "platform ranking from postings, confirmed stack with cross check, platform teams", "the table held website tags only, no ranking, no teams"),
    ("src/exports/repair/people.py", "build_people", "people only from profile or people-search data, valid names, dedupe, employer and title rules, leadership sheet", "the table was filled from news sentences"),
    ("src/exports/repair/news.py", "build_news", "one row per article, real title, real date, facts in a child sheet, source-based grades", "the published date was the collection date and headlines were fragments"),
    ("src/exports/repair/sources.py", "build_sources", "title, publisher and date on every row, job pages apart", "98 percent of rows had no title, none a publisher"),
    ("src/exports/repair/searches.py", "build_searches", "one row per unique query", "the same query was logged once per provider"),
    ("src/exports/repair/signals.py", "build_signals", "signals generated from postings, news, people and technology", "the table was empty"),
    ("src/exports/repair/coverage.py", "build_coverage", "evidence column, last run, no Done without evidence, deliverable claims checked", "coverage and deliverables disagreed"),
    ("src/exports/repair/mail.py", "build_mail", "explicit row when mail and drive are not connected", "the table was empty without a reason"),
    ("src/exports/repair/package.py", "build_members / build_zip", "README generated from the members, validated, CSV and XLSX of every table, Logs folder", "the README promised folders and a workbook that were not in the zip"),
    ("src/account_intelligence/replica/sheets.py", "safe_cell", "a dash at either edge of a cell is removed", "one title ending in a dash made the finished-file check hold back the whole Job postings workbook"),
    ("src/account_intelligence/replica/downloads.py", "_fill", "Download everything runs the repair stage and ships its members", "the tables came from the live screens' fallback queries"),
)  # fmt: skip
