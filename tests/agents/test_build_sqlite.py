"""The v1.12 skill's database builder (docs/skill-1.12): every file of a phase 1 package becomes a
table, the counts match the files, the summaries are SQL over the full tables, and every query is
logged with its base. SYNTHETIC data only."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path("docs/skill-1.12/abm-account-research/assets/build_sqlite.py")


@pytest.fixture(scope="module")
def tool() -> object:
    spec = importlib.util.spec_from_file_location("build_sqlite", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _package(root: Path) -> Path:
    folder = root / "Acme_phase1"
    (folder / "data" / "releases").mkdir(parents=True)
    (folder / "notes").mkdir()
    people = [
        {"name": f"Person {i}", "title": "Director", "location": loc, "security_tag": i == 3}
        for i, loc in enumerate(
            [
                "San Francisco, California, United States",
                "Oakland, California, United States",
                "Boston, Massachusetts, United States",
                "London, England, United Kingdom",
                "San Francisco, California, United States",
                "",
            ],
            start=1,
        )
    ]
    (folder / "data" / "people_grid_rows.json").write_text(json.dumps(people))
    profiles = {
        "profiles": [
            {
                "name": "Person 1",
                "headline": "Salesforce architect",
                "education": [{"school": "State University"}, {"school": "Tech Institute"}],
                "experience": [
                    {"title": "Lead", "description": "Built MuleSoft and Salesforce flows"}
                ],
            },
            {
                "name": "Person 2",
                "headline": "Data lead",
                "education": [{"school": "State University"}],
                "experience": [{"title": "Analyst", "description": "Tableau dashboards, SAP data"}],
            },
            {"name": "Person 3", "headline": "Operations", "education": [], "experience": []},
        ]
    }
    (folder / "data" / "profiles_full.json").write_text(json.dumps(profiles))
    jobs = {
        "count_reported": 4,
        "count_saved": 4,
        "jobs": [
            {
                "title": "Salesforce Developer",
                "status": "open",
                "first_seen": "2026-03-05",
                "description": "Build on Salesforce and MuleSoft",
            },
            {
                "title": "Salesforce Admin",
                "status": "closed",
                "first_seen": "2026-03-20",
                "description": "Administer Salesforce",
            },
            {
                "title": "Data Engineer",
                "status": "open",
                "first_seen": "2026-04-02",
                "description": "SAP and Tableau reporting",
            },
            {
                "title": "Salesforce Developer",
                "status": "open",
                "first_seen": "2026-04-09",
                "description": "Build on Salesforce",
            },
        ],
    }
    (folder / "data" / "jobs_12mo.json").write_text(json.dumps(jobs))
    (folder / "data" / "company_record.json").write_text(
        json.dumps({"domain": "acme.example", "employees": 1200})
    )
    (folder / "data" / "teams.json").write_text(
        json.dumps([{"team": "Platform", "jobs_count": 7, "tech": {"primary": "SAP"}}])
    )
    (folder / "data" / "web_searches.json").write_text(
        json.dumps({"queries": ["a", "b"], "total": 2})
    )
    (folder / "data" / "site_map_news.txt").write_text(
        "https://acme.example/news/1\nhttps://acme.example/news/2\n"
    )
    (folder / "data" / "releases" / "2026-03-01_launch.md").write_text("Acme launches a platform.")
    (folder / "notes" / "pass1.md").write_text("Pass 1 notes.")
    (folder / "pull_log.md").write_text("step 1 ...")
    return folder


def test_every_file_becomes_a_table_whose_rows_match_the_file(tool: object, tmp_path: Path) -> None:
    folder = _package(tmp_path)
    out = tmp_path / "Acme_phase1.sqlite"
    tool.build(SimpleNamespace(folder=str(folder), account="Acme", out=str(out), keywords=None))  # type: ignore[attr-defined]
    con = sqlite3.connect(out)
    files = {
        f: (t, a, b)
        for f, t, a, b in con.execute(
            "SELECT file, table_name, rows_in_file, rows_in_db FROM files"
        )
    }
    assert files["data/people_grid_rows.json"] == ("people_grid_rows", 6, 6)
    assert files["data/profiles_full.json"][1:] == (3, 3)  # the list inside the object
    assert files["data/jobs_12mo.json"][1:] == (4, 4)
    assert files["data/company_record.json"][1:] == (1, 1)  # a single record is one row
    assert (
        files["data/site_map_news.txt"][0] == "text_lines"
        and files["data/site_map_news.txt"][1] == 2
    )
    assert con.execute(
        "SELECT count_reported, count_saved FROM files WHERE file LIKE '%jobs_12mo%'"
    ).fetchone() == (4, 4)
    # nested values: one level flattened, deeper ones kept as JSON text, every row citable
    assert con.execute("SELECT tech_primary FROM teams").fetchone() == ("SAP",)
    assert con.execute(
        "SELECT _row, _source FROM people_grid_rows WHERE name = 'Person 2'"
    ).fetchone() == (2, "people_grid_rows.json")
    docs = {k for (k,) in con.execute("SELECT kind FROM documents")}
    assert docs == {"release", "note", "log"}
    assert (tmp_path / "README_sqlite.md").read_text().startswith("# Acme phase 1 database")


def test_the_summaries_are_sql_over_the_full_tables_and_logged(
    tool: object, tmp_path: Path
) -> None:
    folder = _package(tmp_path)
    out = tmp_path / "Acme_phase1.sqlite"
    tool.build(SimpleNamespace(folder=str(folder), account="Acme", out=str(out), keywords="Boomi"))  # type: ignore[attr-defined]
    con = sqlite3.connect(out)
    assert dict(con.execute("SELECT city, people FROM summary_people_by_location")) == {
        "San Francisco": 2,
        "Oakland": 1,
        "Boston": 1,
        "London": 1,
        "": 1,
    }
    assert con.execute(
        "SELECT people FROM summary_people_by_location WHERE city = 'London'"
    ).fetchone() == (1,)
    assert dict(con.execute("SELECT school, people FROM summary_people_by_school")) == {
        "State University": 2,
        "Tech Institute": 1,
    }
    assert dict(con.execute("SELECT technology, people FROM summary_people_by_technology")) == {
        "Salesforce": 1,
        "MuleSoft": 1,
        "Tableau": 1,
        "SAP": 1,
    }
    got = con.execute(
        "SELECT technology, month, status, postings FROM summary_tech_mentions_by_status_month"
        " WHERE technology = 'Salesforce' ORDER BY month, status"
    ).fetchall()
    assert got == [
        ("Salesforce", "2026-03", "closed", 1),
        ("Salesforce", "2026-03", "open", 1),
        ("Salesforce", "2026-04", "open", 1),
    ]
    # each summary was built by a logged statement that names its base and the base's rows
    log = con.execute("SELECT purpose, base_table, base_rows, sql FROM query_log").fetchall()
    assert any(
        b == "jobs_12mo" and n == 4 and "GROUP BY technology, month, status" in s
        for _, b, n, s in log
    )
    assert any(b == "people_grid_rows" and n == 6 for _, b, n, _s in log)


def test_queries_are_read_only_and_logged_with_their_base(
    tool: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _package(tmp_path)
    out = tmp_path / "Acme_phase1.sqlite"
    tool.build(SimpleNamespace(folder=str(folder), account="Acme", out=str(out), keywords=None))  # type: ignore[attr-defined]
    before = sqlite3.connect(out).execute("SELECT count(*) FROM query_log").fetchone()[0]
    tool.query(
        SimpleNamespace(
            db=str(out),
            purpose="open Salesforce postings",
            base=None,  # type: ignore[attr-defined]
            sql=(
                "SELECT count(*) AS n FROM jobs_12mo"
                " WHERE status = 'open' AND title LIKE '%Salesforce%'"
            ),
        )
    )
    said = capsys.readouterr().out
    assert "base jobs_12mo: 4 rows" in said and "\n2\n" in said + "\n"
    con = sqlite3.connect(out)
    assert con.execute("SELECT count(*) FROM query_log").fetchone()[0] == before + 1
    last = con.execute(
        "SELECT purpose, base_table, base_rows, result FROM query_log ORDER BY id DESC"
    ).fetchone()
    assert last[:3] == ("open Salesforce postings", "jobs_12mo", 4) and '"n": 2' in last[3]
    for sql in (
        "DELETE FROM jobs_12mo",
        "SELECT 1; DROP TABLE files",
        "PRAGMA writable_schema = 1",
    ):
        with pytest.raises(SystemExit):
            tool.query(SimpleNamespace(db=str(out), purpose="x", base=None, sql=sql))  # type: ignore[attr-defined]
    assert sqlite3.connect(out).execute("SELECT count(*) FROM jobs_12mo").fetchone()[0] == 4
    tool.log(SimpleNamespace(db=str(out)))  # type: ignore[attr-defined]
    assert "| Q" in capsys.readouterr().out
