#!/usr/bin/env python3
"""Build {Account}_phase1.sqlite from a phase 1 package, and run logged queries on it.

    python build_sqlite.py build <phase1 folder> [--account NAME] [--out FILE] [--keywords A,B]
    python build_sqlite.py query <db> "<purpose>" "<SELECT ...>" [--base TABLE]
    python build_sqlite.py log   <db>            # the query log as Markdown, for the Summary

`build` loads EVERY file of the package into one database, so a question about the workspace is
answered by SQL over the full table instead of by reading file snippets:

* each `data/*.json` becomes a table named after the file (a list of records, the first list of
  records inside an object, or a single record); nested objects are flattened one level
  (`parent_child`), deeper values are kept as JSON text. `_row` is the record's number in its file
  and `_source` the file name, so any row can be cited;
* a delimited `data/*.txt` grid becomes a table, any other text file one row per line
  (`text_lines`); `data/releases/*.md`, `notes/*.md` and the pull log become `documents`
  (file, kind, date, slug, text), full text;
* `files` lists every file with its table, rows in the file and rows in the database (they must
  match: the build stops otherwise) and the counts the file reports about itself;
  `columns` lists every column with its type and a sample;
* the `summary_*` tables are the answers people ask for, counted by SQL over helper tables
  (`people_locations`, `people_schools`, `people_technology`, `job_technology`) and over the base
  tables: people by country and city, by school, by technology in the profile; technology mentions
  in postings by month and status. They count the rows as stored (duplicates and reposted
  requisitions included): that is the base, and it is stated in the README;
* `query_log` holds every query that built a summary table and every query run with `query`:
  the purpose, the base table and its row count, the SQL, the result. Each number quoted in a
  deliverable names its query id.

Nothing here reads for meaning: it counts what the files hold. Standard library only. The
database, the README and the log use neutral names (no tool or vendor names).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

LANES = ["Salesforce", "MuleSoft", "ServiceNow", "SAP", "Tableau", "Veeva", "Workday"]
LIST_KEYS = ("rows", "items", "data", "results", "records", "people", "profiles", "jobs", "events")
SKIP_TEXT = {"pull_log.md"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "table"


def records(data: Any) -> list[dict[str, Any]] | None:
    """The records a JSON value holds, or None when it holds none."""
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        return rows if rows and len(rows) == len(data) else None
    if not isinstance(data, dict):
        return None
    for key in LIST_KEYS:
        found = records(data.get(key))
        if found:
            return found
    lists = [r for v in data.values() if isinstance(v, list) and (r := records(v))]
    return max(lists, key=len) if lists else [data]


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for k2, v2 in value.items():
                out[f"{slug(key)}_{slug(str(k2))}"] = _cell(v2)
        else:
            out[slug(key)] = _cell(value)
    return out


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def column_type(values: list[Any]) -> str:
    seen = [v for v in values if v is not None and v != ""]
    if seen and all(isinstance(v, int) for v in seen):
        return "INTEGER"
    if seen and all(isinstance(v, (int, float)) for v in seen):
        return "REAL"
    return "TEXT"


class Builder:
    def __init__(self, folder: Path, out: Path, keywords: list[str]) -> None:
        self.folder, self.keywords = folder, keywords
        out.unlink(missing_ok=True)
        self.con = sqlite3.connect(out)
        self.names: set[str] = set()
        self.con.executescript(
            "CREATE TABLE files (file TEXT, table_name TEXT, kind TEXT, rows_in_file INTEGER,"
            " rows_in_db INTEGER, count_reported INTEGER, count_saved INTEGER);"
            "CREATE TABLE columns (table_name TEXT, column_name TEXT, type TEXT, non_null INTEGER,"
            " example TEXT);"
            "CREATE TABLE documents (file TEXT, kind TEXT, date TEXT, slug TEXT, text TEXT);"
            "CREATE TABLE text_lines (file TEXT, line_no INTEGER, text TEXT);"
            "CREATE TABLE query_log (id INTEGER PRIMARY KEY, logged_at TEXT, purpose TEXT,"
            " base_table TEXT, base_rows INTEGER, sql TEXT, result TEXT);"
        )

    def table_name(self, stem: str) -> str:
        base, name, n = slug(stem), slug(stem), 1
        while name in self.names or name in {
            "files",
            "columns",
            "documents",
            "text_lines",
            "query_log",
        }:
            n += 1
            name = f"{base}_{n}"
        self.names.add(name)
        return name

    def load_table(self, name: str, source: str, rows: list[dict[str, Any]]) -> None:
        flat = [flatten(r) for r in rows]
        cols: list[str] = []
        for r in flat:
            cols += [c for c in r if c not in cols]
        cols = [c for c in cols if c not in ("_row", "_source")]
        types = {c: column_type([r.get(c) for r in flat]) for c in cols}
        ddl = ", ".join(f'"{c}" {types[c]}' for c in cols)
        self.con.execute(
            f'CREATE TABLE "{name}" (_row INTEGER, _source TEXT{", " + ddl if ddl else ""})'
        )
        marks = ",".join("?" * (len(cols) + 2))
        self.con.executemany(
            f'INSERT INTO "{name}" VALUES ({marks})',
            [[i, source, *[r.get(c) for c in cols]] for i, r in enumerate(flat, start=1)],
        )
        for c in cols:
            vals = [r.get(c) for r in flat if r.get(c) not in (None, "")]
            self.con.execute(
                "INSERT INTO columns VALUES (?,?,?,?,?)",
                (name, c, types[c], len(vals), str(vals[0])[:80] if vals else ""),
            )

    def add_file(self, source: str, table: str, kind: str, rows: int, data: Any = None) -> None:
        got = self.con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        reported = saved = None
        if isinstance(data, dict):
            reported, saved = data.get("count_reported"), data.get("count_saved")
        self.con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?)",
            (source, table, kind, rows, got, reported, saved),
        )
        if got != rows:
            sys.exit(f"{source}: {rows} records in the file but {got} in the database")

    def load_data(self) -> None:
        for path in sorted((self.folder / "data").glob("*")):
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                rows = records(data)
                if rows is None:  # a scalar or a list of non-records: keep it as one text row
                    rows = [{"value": json.dumps(data, ensure_ascii=False)}]
                name = self.table_name(path.stem)
                self.load_table(name, path.name, rows)
                self.add_file(f"data/{path.name}", name, "json", len(rows), data)
            elif path.suffix == ".txt":
                self.load_text(path)

    def load_text(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        delimiter = next((d for d in ("\t", "|", ",") if lines and lines[0].count(d) >= 2), None)
        if delimiter and len(lines) > 1:
            rows = list(csv.DictReader(lines, delimiter=delimiter))
            name = self.table_name(path.stem)
            self.load_table(name, path.name, rows)
            self.add_file(f"data/{path.name}", name, "grid", len(rows))
            return
        self.con.executemany(
            "INSERT INTO text_lines VALUES (?,?,?)",
            [(f"data/{path.name}", i, ln) for i, ln in enumerate(text.splitlines(), start=1)],
        )
        total = len(text.splitlines())
        got = self.con.execute(
            "SELECT count(*) FROM text_lines WHERE file = ?", (f"data/{path.name}",)
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,NULL,NULL)",
            (f"data/{path.name}", "text_lines", "text", total, got),
        )

    def load_documents(self) -> None:
        for sub, kind in (("data/releases", "release"), ("notes", "note"), (".", "log")):
            for path in sorted((self.folder / sub).glob("*.md")):
                if sub == "." and path.name not in SKIP_TEXT:
                    continue
                m = re.match(r"(\d{4}-\d{2}-\d{2})_(.*)\.md$", path.name)
                self.con.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?)",
                    (
                        str(path.relative_to(self.folder)),
                        kind,
                        m.group(1) if m else None,
                        m.group(2) if m else path.stem,
                        path.read_text(encoding="utf-8", errors="replace"),
                    ),
                )

    # ---- summaries: helper tables in Python, the answers in SQL, every statement logged ----

    def table_like(self, *words: str) -> str | None:
        found = [
            (self.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0], t)
            for (t,) in self.con.execute("SELECT table_name FROM files WHERE kind != 'text'")
            if any(w in t for w in words)
        ]
        return max(found)[1] if found else None

    def column_like(self, table: str, *words: str) -> str | None:
        for (c,) in self.con.execute(
            "SELECT column_name FROM columns WHERE table_name = ?", (table,)
        ):
            if any(w in c for w in words):
                return c
        return None

    def log(self, purpose: str, base: str | None, sql: str, result: str = "") -> None:
        rows = self.con.execute(f'SELECT count(*) FROM "{base}"').fetchone()[0] if base else None
        self.con.execute(
            "INSERT INTO query_log (logged_at, purpose, base_table, base_rows, sql, result)"
            " VALUES (?,?,?,?,?,?)",
            (
                dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                purpose,
                base,
                rows,
                sql,
                result,
            ),
        )

    def summarize(self) -> None:
        sql = "CREATE TABLE summary_counts AS SELECT table_name, rows_in_db AS rows FROM files"
        self.con.execute(sql)
        self.log("rows per table", None, sql)
        people = self.table_like("people_grid", "people", "profile")
        profiles = self.table_like("profiles_full", "profile")
        if people and (loc := self.column_like(people, "location", "city")):
            self.con.execute(
                "CREATE TABLE people_locations (person_row INTEGER, location TEXT, city TEXT, country TEXT)"
            )
            for row, text in self.con.execute(f'SELECT _row, "{loc}" FROM "{people}"').fetchall():
                parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
                self.con.execute(
                    "INSERT INTO people_locations VALUES (?,?,?,?)",
                    (row, text, parts[0] if parts else "", parts[-1] if len(parts) > 1 else ""),
                )
            self.summary(
                "people by country and city",
                people,
                "CREATE TABLE summary_people_by_location AS SELECT country, city, count(*) AS people"
                " FROM people_locations GROUP BY country, city ORDER BY people DESC",
            )
        if profiles:
            self.profile_summaries(profiles)
        self.job_summary()

    def summary(self, purpose: str, base: str | None, sql: str) -> None:
        self.con.execute(sql)
        name = re.search(r"CREATE TABLE (\w+)", sql).group(1)  # type: ignore[union-attr]
        n = self.con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        self.log(purpose, base, sql, f"{n} rows in {name}")

    def profile_summaries(self, profiles: str) -> None:
        cols = [
            c
            for (c,) in self.con.execute(
                "SELECT column_name FROM columns WHERE table_name=? AND type='TEXT'", (profiles,)
            )
        ]
        self.con.execute("CREATE TABLE people_schools (person_row INTEGER, school TEXT)")
        self.con.execute("CREATE TABLE people_technology (person_row INTEGER, technology TEXT)")
        edu = [c for c in cols if "educ" in c or "school" in c]
        for row in self.con.execute(f'SELECT * FROM "{profiles}"').fetchall():
            by = dict(
                zip(
                    [
                        d[0]
                        for d in self.con.execute(f'SELECT * FROM "{profiles}" LIMIT 0').description
                    ],
                    row,
                )
            )
            text = " ".join(str(by[c]) for c in cols if by.get(c)).lower()
            for kw in self.keywords:
                if re.search(rf"\b{re.escape(kw.lower())}\b", text):
                    self.con.execute("INSERT INTO people_technology VALUES (?,?)", (by["_row"], kw))
            for c in edu:
                for school in schools(by.get(c)):
                    self.con.execute(
                        "INSERT INTO people_schools VALUES (?,?)", (by["_row"], school)
                    )
        self.summary(
            "people by technology in the profile text",
            profiles,
            "CREATE TABLE summary_people_by_technology AS SELECT technology, count(DISTINCT person_row) AS people"
            " FROM people_technology GROUP BY technology ORDER BY people DESC",
        )
        self.summary(
            "people by school",
            profiles,
            "CREATE TABLE summary_people_by_school AS SELECT school, count(DISTINCT person_row) AS people"
            " FROM people_schools GROUP BY school ORDER BY people DESC",
        )

    def job_summary(self) -> None:
        jobs = self.table_like("jobs")
        if not jobs:
            return
        date = self.column_like(jobs, "first_seen", "posted", "created", "date")
        status = self.column_like(jobs, "status", "active", "state")
        cols = [
            c
            for (c,) in self.con.execute(
                "SELECT column_name FROM columns WHERE table_name=? AND type='TEXT'", (jobs,)
            )
        ]
        self.con.execute(
            "CREATE TABLE job_technology (job_row INTEGER, technology TEXT, month TEXT, status TEXT)"
        )
        names = [d[0] for d in self.con.execute(f'SELECT * FROM "{jobs}" LIMIT 0').description]
        for row in self.con.execute(f'SELECT * FROM "{jobs}"').fetchall():
            by = dict(zip(names, row))
            text = " ".join(str(by[c]) for c in cols if by.get(c) and c != status).lower()
            month = str(by.get(date) or "")[:7] if date else ""
            for kw in self.keywords:
                if re.search(rf"\b{re.escape(kw.lower())}\b", text):
                    self.con.execute(
                        "INSERT INTO job_technology VALUES (?,?,?,?)",
                        (by["_row"], kw, month, str(by.get(status) or "") if status else ""),
                    )
        self.summary(
            "technology mentions in postings by month and status",
            jobs,
            "CREATE TABLE summary_tech_mentions_by_status_month AS SELECT technology, month, status,"
            " count(*) AS postings FROM job_technology GROUP BY technology, month, status"
            " ORDER BY technology, month",
        )


def schools(value: Any) -> list[str]:
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return []
    items = data if isinstance(data, list) else [data]
    out = []
    for item in items:
        if isinstance(item, dict):
            for key in ("school", "school_name", "institution", "name"):
                if isinstance(item.get(key), str) and item[key].strip():
                    out.append(item[key].strip())
                    break
        elif isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def readme(db: Path, con: sqlite3.Connection, account: str) -> str:
    files = con.execute("SELECT file, table_name, rows_in_db FROM files ORDER BY file").fetchall()
    summaries = [
        t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'summary_%'")
    ]
    lines = [
        f"# {account} phase 1 database",
        "",
        f"`{db.name}` holds every file of the phase 1 package as a table. Answer questions about the",
        "account with SQL over the full table, never from file snippets, and state the base count",
        "queried. Quote a number only with its query id from `query_log`.",
        "",
        "Open: `sqlite3 " + db.name + "` or Python's `sqlite3`. Run logged queries with",
        f'`python build_sqlite.py query {db.name} "purpose" "SELECT ..."`.',
        "",
        "## Tables",
        "",
        "| File | Table | Rows |",
        "|---|---|---|",
        *[f"| {f} | {t} | {n} |" for f, t, n in files],
        "",
        "`documents` (full text of releases, notes and the pull log), `text_lines`, `columns` (every",
        "column, type and a sample), `files`, `query_log`.",
        "",
        "## Summary tables (counts of the rows as stored: duplicates and reposted requisitions included)",
        "",
        *[f"- `{t}`" for t in summaries],
        "",
        "Every row has `_row` (its number in its file) and `_source` (the file).",
    ]
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> None:
    folder = Path(args.folder)
    account = args.account or re.sub(r"_?phase1$", "", folder.resolve().name) or "Account"
    out = Path(args.out) if args.out else folder / f"{slug(account).title()}_phase1.sqlite"
    keywords = LANES + [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    b = Builder(folder, out, keywords)
    b.load_data()
    b.load_documents()
    b.summarize()
    b.con.commit()
    (out.parent / "README_sqlite.md").write_text(readme(out, b.con, account), encoding="utf-8")
    n = b.con.execute("SELECT count(*), sum(rows_in_db) FROM files").fetchone()
    print(f"{out}: {n[0]} files, {n[1]} rows; every file's rows match the database")


def query(args: argparse.Namespace) -> None:
    sql = args.sql.strip().rstrip(";")
    if not re.match(r"(?is)^(select|with)\b", sql) or re.search(
        r"(?i)\b(insert|update|delete|drop|alter|create|attach|pragma)\b", sql
    ):
        sys.exit("only SELECT queries are run")
    ro = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    cur = ro.execute(sql)
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    base = args.base or next(iter(re.findall(r'(?i)\bfrom\s+"?(\w+)"?', sql)), None)
    base_rows = ro.execute(f'SELECT count(*) FROM "{base}"').fetchone()[0] if base else None
    ro.close()
    con = sqlite3.connect(args.db)
    result = json.dumps([dict(zip(names, r)) for r in rows[:20]], ensure_ascii=False, default=str)
    cur = con.execute(
        "INSERT INTO query_log (logged_at, purpose, base_table, base_rows, sql, result) VALUES (?,?,?,?,?,?)",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            args.purpose,
            base,
            base_rows,
            sql,
            result,
        ),
    )
    con.commit()
    print(f"query {cur.lastrowid}: {len(rows)} rows; base {base}: {base_rows} rows")
    print(" | ".join(names))
    for r in rows[:50]:
        print(" | ".join("" if v is None else str(v) for v in r))


def log(args: argparse.Namespace) -> None:
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print("| Query | Purpose | Base | SQL |\n|---|---|---|---|")
    for qid, purpose, base, base_rows, sql in con.execute(
        "SELECT id, purpose, base_table, base_rows, sql FROM query_log ORDER BY id"
    ):
        print(
            f"| Q{qid} | {purpose} | {base or ''} ({base_rows if base_rows is not None else ''} rows) | `{' '.join(sql.split())}` |"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("folder")
    b.add_argument("--account")
    b.add_argument("--out")
    b.add_argument("--keywords", help="more technologies to count, comma separated")
    b.set_defaults(fn=build)
    q = sub.add_parser("query")
    q.add_argument("db")
    q.add_argument("purpose")
    q.add_argument("sql")
    q.add_argument("--base")
    q.set_defaults(fn=query)
    lg = sub.add_parser("log")
    lg.add_argument("db")
    lg.set_defaults(fn=log)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
