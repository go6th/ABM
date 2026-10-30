"""Every acceptance check: a passing world, and one failing variant per check."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from exports.repair import checks as C
from exports.repair import core as K
from exports.repair import package as P
from exports.repair.core import EntityRule, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.records import CitedFact, CoverageRecord, DeliverableClaim, RepairInput

RUN = date(2026, 10, 30)


def profile() -> WorkspaceProfile:
    platforms = SimpleNamespace(
        lanes=[SimpleNamespace(name="Alphaflow")],
        ranking=[SimpleNamespace(name="Betabase"), SimpleNamespace(name="Alphaflow")],
    )
    return WorkspaceProfile(
        account_name="Zephyr Textiles Inc",
        account_short="Zephyr",
        domain="zephyr.example",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
        account_label="Zephyr",
        entities=(
            EntityRule("Zephyr Marine", ("marine",), counts_as_account=True),
            EntityRule("Old Looms", ("looms",), counts_as_account=False),
        ),
        platforms=platforms,
    )


def row(columns: tuple[str, ...], **values: str) -> dict[str, str]:
    """A fully filled row: unnamed columns get the plain placeholder."""
    return {c: values.get(c, K.NOT_FOUND) for c in columns}


def vals(columns: tuple[str, ...], mapping: dict[str, str]) -> dict[str, str]:
    return row(columns, **mapping)


def make_jobs() -> RepairedTable:
    cols = K.JOBS_COLUMNS
    rows = [
        vals(
            cols,
            {
                "Posting ID": "1001",
                "Title": "Platform Engineer",
                "Entity": "Zephyr",
                "Platforms named": "Alphaflow",
                "Status": "Open",
                "Posted": "2026-09-01",
                "Collected": "2026-10-30",
            },
        ),
        vals(
            cols,
            {
                "Posting ID": "1002",
                "Title": "Data Lead",
                "Entity": "Zephyr Marine",
                "Platforms named": "Alphaflow; Betabase",
                "Status": "Closed (not found on site, 2026-10-30)",
                "Posted": "2026-08-01",
                "Collected": "2026-10-30",
            },
        ),
        vals(
            cols,
            {
                "Posting ID": "1003",
                "Title": "Analyst",
                "Entity": "Old Looms",
                "Platforms named": "Alphaflow",
                "Status": "Open",
                "Posted": "2026-07-01",
                "Collected": "2026-10-30",
            },
        ),
    ]
    all_cols = K.JOBS_ALL_COLUMNS
    all_rows = [
        vals(
            all_cols,
            {
                "Posting ID": r["Posting ID"],
                "Title": r["Title"],
                "Entity": r["Entity"],
                "Status": r["Status"],
            },
        )
        for r in rows
    ] + [
        vals(
            all_cols, {"Posting ID": "2001", "Title": "Cook", "Entity": "Zephyr", "Status": "Open"}
        )
    ]
    resolved = [
        SimpleNamespace(
            platforms=("Alphaflow",),
            entity="Zephyr",
            counts_as_account=True,
            title="Platform Engineer",
            status="Open",
            outside_window=False,
        ),
        SimpleNamespace(
            platforms=("Alphaflow", "Betabase"),
            entity="Zephyr Marine",
            counts_as_account=True,
            title="Data Lead",
            status="Closed",
            outside_window=False,
        ),
        SimpleNamespace(
            platforms=("Alphaflow",),
            entity="Old Looms",
            counts_as_account=False,
            title="Analyst",
            status="Open",
            outside_window=False,
        ),
    ]
    return RepairedTable(
        key="jobs",
        title="Job postings",
        sheets=[
            Sheet("Job postings", cols, rows),
            Sheet("All postings (unfiltered)", all_cols, all_rows),
        ],
        stats={
            "per_platform": {
                "Alphaflow": {
                    "postings": 2,
                    "open": 1,
                    "build": 0,
                    "admin": 0,
                    "tool": 0,
                    "user": 0,
                },
                "Betabase": {
                    "postings": 1,
                    "open": 0,
                    "build": 0,
                    "admin": 0,
                    "tool": 0,
                    "user": 0,
                },
            },
            "resolved": resolved,
        },
    )


def make_people() -> RepairedTable:
    cols = K.PEOPLE_COLUMNS
    rows = [
        vals(
            cols,
            {
                "Name": "Mary Jones",
                "Entity": "Zephyr",
                "Found by": "people_search",
                "LinkedIn": "https://social.example/in/mary-jones",
            },
        ),
        vals(
            cols,
            {
                "Name": "Carlos de la Cruz",
                "Entity": "Zephyr",
                "Found by": "profile",
                "LinkedIn": K.NOT_FOUND,
            },
        ),
    ]
    return RepairedTable(
        key="people",
        title="People",
        sheets=[Sheet("People", cols, rows), Sheet("Leadership", K.LEADERSHIP_COLUMNS, [])],
        stats={"total": 2, "meets_seniority": 1, "consent_missing": 0, "from_news": 0},
    )


def make_tech() -> RepairedTable:
    rank = K.RANKING_COLUMNS
    stack = K.STACK_COLUMNS
    return RepairedTable(
        key="tech",
        title="Tech stack and teams",
        sheets=[
            Sheet(
                "Platform Ranking",
                rank,
                [
                    vals(rank, {"Platform": "Alphaflow", "Rank": "A"}),
                    vals(rank, {"Platform": "Betabase", "Rank": "B"}),
                ],
            ),
            Sheet(
                "Confirmed stack",
                stack,
                [
                    vals(stack, {"Technology": "Alphaflow", "Cross check": "Both"}),
                    vals(stack, {"Technology": "Gridlock", "Cross check": "Detection only"}),
                ],
            ),
            Sheet("Platform teams", K.TEAM_COLUMNS, []),
        ],
    )


def make_news() -> RepairedTable:
    cols = K.NEWS_COLUMNS
    rows = [
        vals(
            cols,
            {
                "Article ID": "N1",
                "Headline": "Zephyr opens plant",
                "Published": "2026-10-01",
                "Collected": "2026-10-30",
                "Source URL": "https://news.example/a",
            },
        ),
        vals(
            cols,
            {
                "Article ID": "N2",
                "Headline": "Zephyr hires",
                "Published": K.UNKNOWN,
                "Collected": "2026-10-30",
                "Source URL": "https://news.example/b",
            },
        ),
    ]
    return RepairedTable(
        "news", "News", [Sheet("News", cols, rows), Sheet("News facts", K.NEWS_FACT_COLUMNS, [])]
    )


def make_sources() -> RepairedTable:
    cols = K.SOURCES_COLUMNS
    rows = [
        vals(
            cols,
            {
                "Code": "S-0001",
                "Title": "Annual report",
                "Publisher": "Zephyr",
                "Published": "2026-03-01",
                "Collected": "2026-10-30",
                "Link": "https://zephyr.example/ar",
            },
        ),
        vals(
            cols,
            {
                "Code": "S-0002",
                "Title": "Trade piece",
                "Publisher": "Textile Weekly",
                "Published": K.UNKNOWN,
                "Collected": "2026-10-30",
                "Link": "https://tw.example/p",
            },
        ),
    ]
    return RepairedTable(
        "sources", "Sources", [Sheet("Sources", cols, rows), Sheet("Job pages", cols, [])]
    )


def make_searches() -> RepairedTable:
    cols = K.SEARCH_COLUMNS
    rows = [
        vals(cols, {"Query": "Zephyr Textiles jobs"}),
        vals(cols, {"Query": "zephyr  textiles hiring"}),
    ]
    return RepairedTable("searches", "Web searches", [Sheet("Web searches", cols, rows)])


def make_signals() -> RepairedTable:
    cols = K.SIGNAL_COLUMNS
    rows = [
        vals(
            cols,
            {
                "Signal": "New CFO named",
                "Type": "Leadership change",
                "Evidence (source codes)": "S1",
            },
        ),
        vals(
            cols,
            {
                "Signal": "New CFO named",
                "Type": "Funding / financial",
                "Evidence (source codes)": "S2",
            },
        ),
    ]
    return RepairedTable("signals", "Signals", [Sheet("Signals", cols, rows)])


def make_coverage() -> RepairedTable:
    cols = K.COVERAGE_COLUMNS
    rows = [
        vals(cols, {"#": "1", "Research item": "Jobs", "Status": "Done"}),
        vals(cols, {"#": "2", "Research item": "People", "Status": "Not done"}),
    ]
    return RepairedTable(
        "coverage", "Coverage", [Sheet("Coverage", cols, rows)], stats={"mismatches": []}
    )


def make_mail() -> RepairedTable:
    cols = K.MAIL_COLUMNS
    rows = [vals(cols, {"Title": K.not_collected("mail and drive not connected")})]
    return RepairedTable(
        "mail", "From your mail and drive", [Sheet("Mail", cols, rows, private=True)], internal=True
    )


def make_world() -> tuple[WorkspaceProfile, dict[str, RepairedTable], RepairInput]:
    tables = {
        t.key: t
        for t in (
            make_jobs(),
            make_people(),
            make_tech(),
            make_news(),
            make_sources(),
            make_searches(),
            make_signals(),
            make_coverage(),
            make_mail(),
        )
    }
    inp = RepairInput(
        cited_facts=[
            CitedFact("f1", "Confirmed", ("S1",)),
            CitedFact("f2", "Probable", ("S-0002",)),
        ],
        quoted_counts={"jobs.account_postings": 2, "people.total": 2, "people.meets_seniority": 1},
    )
    return profile(), tables, inp


def by_name(results: list[C.CheckResult]) -> dict[str, C.CheckResult]:
    return {r.name: r for r in results}


def failed(results: list[C.CheckResult]) -> set[str]:
    return {r.name for r in results if not r.passed}


def run(prof: Any, tables: Any, inp: Any, **kw: Any) -> list[C.CheckResult]:
    return C.run_checks(prof, tables, inp, **kw)


def zip_of(prof: WorkspaceProfile, tables: dict[str, RepairedTable]) -> bytes:
    return P.build_zip(prof, tables, deliverables={"Summary.docx": b"d"})


# ---- the passing world ------------------------------------------------------------------------


def test_good_world_passes_every_check_with_the_zip() -> None:
    prof, tables, inp = make_world()
    results = run(prof, tables, inp, zip_bytes=zip_of(prof, tables))
    assert failed(results) == set(), [(r.name, r.detail) for r in results if not r.passed]
    assert C.all_passed(results)
    names = {r.name for r in results}
    assert {
        "no_duplicate_keys",
        "no_empty_cell_without_placeholder",
        "published_not_collected",
        "people_names_valid",
        "people_none_from_news",
        "people_linkedin_filled",
        "jobs_title_not_platform",
        "jobs_status",
        "jobs_entity",
        "jobs_counts_account_only",
        "tech_ranking_has_lane_platforms",
        "tech_cross_check_filled",
        "sources_fields_filled",
        "citations_resolve",
        "confirmed_facts_cite_source",
        "coverage_agrees_with_deliverables",
        "zip_contains_readme_paths",
        "table_counts_match_deliverables",
    } <= names
    assert by_name(results)["table_counts_match_deliverables"].numbers["compared"] == 3
    assert by_name(results)["jobs_counts_account_only"].numbers["account_postings_by_platform"] == 3


def test_without_a_zip_the_zip_check_is_skipped_and_passes() -> None:
    prof, tables, inp = make_world()
    r = by_name(run(prof, tables, inp))["zip_contains_readme_paths"]
    assert r.passed and "skipped" in r.detail


def test_all_passed_is_false_for_empty_and_for_any_failure() -> None:
    assert C.all_passed([]) is False
    assert C.all_passed([C.CheckResult("a", True, ""), C.CheckResult("b", False, "")]) is False


def test_a_crashing_check_is_reported_failed_not_raised() -> None:
    prof, tables, inp = make_world()
    inp.cited_facts = None  # type: ignore[assignment]
    results = run(prof, tables, inp)
    crashed = by_name(results)["sources"]
    assert not crashed.passed and "check crashed" in crashed.detail


# ---- one failing variant per check ------------------------------------------------------------

Mutation = Callable[[WorkspaceProfile, dict[str, RepairedTable], RepairInput], None]


def sheet_of(tables: dict[str, RepairedTable], key: str, name: str | None = None) -> Sheet:
    t = tables[key]
    return t.sheets[0] if name is None else next(s for s in t.sheets if s.name == name)


def m_dup_job(prof: Any, tables: Any, inp: Any) -> None:
    s = sheet_of(tables, "jobs", "Job postings")
    s.rows.append(dict(s.rows[0]))


def m_dup_person(prof: Any, tables: Any, inp: Any) -> None:
    s = sheet_of(tables, "people")
    s.rows.append(dict(s.rows[1], Name="Carlos De La Cruz"))


def m_dup_news_url(prof: Any, tables: Any, inp: Any) -> None:
    s = sheet_of(tables, "news", "News")
    s.rows[1]["Source URL"] = "https://www.news.example/a/?utm_source=x"


def m_dup_search(prof: Any, tables: Any, inp: Any) -> None:
    sheet_of(tables, "searches").rows[1]["Query"] = "Zephyr  textiles JOBS!"


def m_dup_signal(prof: Any, tables: Any, inp: Any) -> None:
    sheet_of(tables, "signals").rows[1]["Type"] = "leadership change"
    sheet_of(tables, "signals").rows[1]["Signal"] = "new cfo named"


def m_dup_coverage(prof: Any, tables: Any, inp: Any) -> None:
    sheet_of(tables, "coverage").rows[1]["#"] = "1"


def m_dup_source(prof: Any, tables: Any, inp: Any) -> None:
    sheet_of(tables, "sources", "Sources").rows[1]["Link"] = "https://zephyr.example/ar/"


def m_dup_tech(prof: Any, tables: Any, inp: Any) -> None:
    sheet_of(tables, "tech", "Confirmed stack").rows[1]["Technology"] = "alphaflow"


@pytest.mark.parametrize(
    "mutate",
    [
        m_dup_job,
        m_dup_person,
        m_dup_news_url,
        m_dup_search,
        m_dup_signal,
        m_dup_coverage,
        m_dup_source,
        m_dup_tech,
    ],
)
def test_duplicate_keys_fail(mutate: Mutation) -> None:
    prof, tables, inp = make_world()
    mutate(prof, tables, inp)
    r = by_name(run(prof, tables, inp))["no_duplicate_keys"]
    assert not r.passed and "repeated" in r.detail


def test_duplicate_linkedin_url_fails_even_with_different_names() -> None:
    prof, tables, inp = make_world()
    s = sheet_of(tables, "people")
    s.rows.append(dict(s.rows[0], Name="Someone Else"))
    assert not by_name(run(prof, tables, inp))["no_duplicate_keys"].passed


def test_empty_cell_fails_and_names_table_and_column() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "news", "News").rows[0]["Headline"] = ""
    sheet_of(tables, "jobs", "Job postings").rows[0].pop("Location")
    r = by_name(run(prof, tables, inp))["no_empty_cell_without_placeholder"]
    assert not r.passed
    assert "News/News row 2 column 'Headline'" in r.detail
    assert r.numbers["problems"] == 2


def test_empty_cell_report_lists_at_most_five_offenders() -> None:
    prof, tables, inp = make_world()
    for s in tables["coverage"].sheets:
        for r in s.rows:
            for c in s.columns:
                r[c] = ""
    r = by_name(run(prof, tables, inp))["no_empty_cell_without_placeholder"]
    assert r.detail.count("empty") >= 5 and "more)" in r.detail
    assert r.detail.count("column") == 5


@pytest.mark.parametrize(
    "bad", ["not found", "Not collected:", "Not collected:   ", "Unknown ", "Closed (not found"]
)
def test_malformed_placeholder_fails(bad: str) -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "news", "News").rows[0]["Headline"] = bad
    r = by_name(run(prof, tables, inp))["no_empty_cell_without_placeholder"]
    if bad.strip() == "Unknown":  # surrounding space only: still the exact marker
        assert r.passed
    else:
        assert not r.passed and "malformed placeholder" in r.detail


def test_real_text_that_starts_like_a_marker_word_is_not_flagged() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "news", "News").rows[0]["Headline"] = "Unknown buyer acquires plant"
    assert by_name(run(prof, tables, inp))["no_empty_cell_without_placeholder"].passed


def test_published_equal_to_collected_fails_unless_allowed() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "news", "News").rows[0]["Published"] = "2026-10-30"
    sheet_of(tables, "jobs", "Job postings").rows[0]["Posted"] = "2026-10-30 08:00"
    sheet_of(tables, "sources", "Sources").rows[0]["Published"] = "2026-10-30"
    r = by_name(run(prof, tables, inp))["published_not_collected"]
    assert not r.passed and r.numbers["problems"] == 3
    ok = by_name(run(prof, tables, inp, allowed={"N1", "1001", "S-0001"}))[
        "published_not_collected"
    ]
    assert ok.passed


def test_people_invalid_names_fail() -> None:
    prof, tables, inp = make_world()
    s = sheet_of(tables, "people")
    s.rows[0]["Name"] = "Chief Financial Officer"
    s.rows[1]["Name"] = "Joins Zephyr as vice president, finance"
    r = by_name(run(prof, tables, inp))["people_names_valid"]
    assert not r.passed and r.numbers["problems"] == 2


def test_people_consent_placeholder_row_is_not_a_name_error() -> None:
    prof, tables, inp = make_world()
    cols = K.PEOPLE_COLUMNS
    tables["people"].sheets[0].rows[:] = [
        row(cols, Name=K.not_collected("people data consent needed"))
    ]
    tables["people"].stats = {"consent_missing": 1, "total": 0}
    results = by_name(run(prof, tables, inp))
    assert results["people_names_valid"].passed and results["people_linkedin_filled"].passed


def test_people_from_news_fail_by_found_by_and_by_statistics() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "people").rows[0]["Found by"] = "News article"
    assert not by_name(run(prof, tables, inp))["people_none_from_news"].passed
    prof, tables, inp = make_world()
    tables["people"].stats["from_news"] = 2
    r = by_name(run(prof, tables, inp))["people_none_from_news"]
    assert not r.passed and r.numbers["from_news"] == 2


@pytest.mark.parametrize("link", ["", "ftp://x.example/in/a", "linkedin.example/in/a", "Not Found"])
def test_people_linkedin_must_be_a_link_or_not_found(link: str) -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "people").rows[0]["LinkedIn"] = link
    assert not by_name(run(prof, tables, inp))["people_linkedin_filled"].passed


def test_local_name_validator_when_no_names_module(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        C.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError(name))
    )
    ok = C._name_validator()
    assert ok("Mary Jones") and ok("Ludwig van Beethoven") and ok("Anne-Marie O'Neil")
    assert (
        not ok("Madonna")
        and not ok("Dr. Who, CEO")
        and not ok("John Smith 3rd")
        and not ok("a b c d e f")
    )


def test_names_module_is_used_when_present(monkeypatch: Any) -> None:
    fake = SimpleNamespace(is_person_name=lambda n: n == "Only This")
    monkeypatch.setattr(C.importlib, "import_module", lambda name: fake)
    prof, tables, inp = make_world()
    assert not by_name(run(prof, tables, inp))["people_names_valid"].passed


def test_job_title_equal_to_platform_name_fails() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "jobs", "Job postings").rows[0]["Title"] = "ALPHAFLOW"
    r = by_name(run(prof, tables, inp))["jobs_title_not_platform"]
    assert not r.passed and "platform name" in r.detail


def test_job_title_check_falls_back_to_the_default_dictionary() -> None:
    from parity.platforms import default_platforms

    name = default_platforms().lane_names[0]
    prof, tables, inp = make_world()
    object.__setattr__(prof, "platforms", None)
    sheet_of(tables, "jobs", "Job postings").rows[0]["Title"] = name.upper()
    assert not by_name(run(prof, tables, inp))["jobs_title_not_platform"].passed


@pytest.mark.parametrize("status", ["Pending", "open", "", "Unknown"])
def test_job_status_must_be_open_or_closed(status: str) -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "jobs", "Job postings").rows[0]["Status"] = status
    assert not by_name(run(prof, tables, inp))["jobs_status"].passed


def test_job_entity_required_on_every_row_of_both_sheets() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "jobs", "All postings (unfiltered)").rows[0]["Entity"] = ""
    r = by_name(run(prof, tables, inp))["jobs_entity"]
    assert not r.passed and "All postings" in r.detail


def test_job_counts_must_come_from_account_rows_and_match_statistics() -> None:
    prof, tables, inp = make_world()
    # a divested row counted as the account: statistics say 3 where account rows give 2
    tables["jobs"].stats["per_platform"]["Alphaflow"]["postings"] = 3
    r = by_name(run(prof, tables, inp))["jobs_counts_account_only"]
    assert not r.passed and "alphaflow postings: statistics 3, account rows 2" in r.detail


def test_job_counts_open_mismatch_and_resolved_mismatch_fail() -> None:
    prof, tables, inp = make_world()
    tables["jobs"].stats["per_platform"]["Alphaflow"]["open"] = 2
    assert not by_name(run(prof, tables, inp))["jobs_counts_account_only"].passed
    prof, tables, inp = make_world()
    tables["jobs"].stats["resolved"][2].counts_as_account = True
    r = by_name(run(prof, tables, inp))["jobs_counts_account_only"]
    assert not r.passed and "resolved postings 3" in r.detail


def test_outside_window_rows_are_not_counted() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "jobs", "Job postings").rows[0]["Posted"] = K.OUTSIDE_WINDOW
    tables["jobs"].stats["per_platform"]["Alphaflow"] = {"postings": 1, "open": 0}
    tables["jobs"].stats["resolved"][0].outside_window = True
    assert by_name(run(prof, tables, inp))["jobs_counts_account_only"].passed


def test_job_counts_without_statistics_use_sheet_counts_only() -> None:
    prof, tables, inp = make_world()
    del tables["jobs"].stats["per_platform"]
    r = by_name(run(prof, tables, inp))["jobs_counts_account_only"]
    assert r.passed and "note" in r.numbers


def test_tech_ranking_must_hold_every_platform_found_in_postings() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "tech", "Platform Ranking").rows.pop(1)
    r = by_name(run(prof, tables, inp))["tech_ranking_has_lane_platforms"]
    assert not r.passed and "Betabase" in r.detail


def test_tech_ranking_check_is_skipped_without_job_statistics() -> None:
    prof, tables, inp = make_world()
    del tables["jobs"]
    r = by_name(run(prof, tables, inp))["tech_ranking_has_lane_platforms"]
    assert r.passed and "skipped" in r.detail


@pytest.mark.parametrize("value", ["", "Maybe", "Not found"])
def test_tech_cross_check_must_be_filled_on_every_row(value: str) -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "tech", "Confirmed stack").rows[1]["Cross check"] = value
    assert not by_name(run(prof, tables, inp))["tech_cross_check_filled"].passed


def test_sources_must_have_title_publisher_published() -> None:
    prof, tables, inp = make_world()
    sheet_of(tables, "sources", "Sources").rows[0]["Publisher"] = " "
    r = by_name(run(prof, tables, inp))["sources_fields_filled"]
    assert not r.passed and "S-0001: Publisher empty" in r.detail


def test_citations_must_resolve_and_accept_both_code_spellings() -> None:
    prof, tables, inp = make_world()
    assert by_name(run(prof, tables, inp))["citations_resolve"].passed  # S1 vs S-0001, S-0002
    inp.cited_facts.append(CitedFact("f3", "Probable", ("S9",)))
    r = by_name(run(prof, tables, inp))["citations_resolve"]
    assert not r.passed and "S9" in r.detail


def test_confirmed_fact_without_a_source_fails() -> None:
    prof, tables, inp = make_world()
    inp.cited_facts.append(CitedFact("f3", "confirmed", ()))
    r = by_name(run(prof, tables, inp))["confirmed_facts_cite_source"]
    assert not r.passed and "f3" in r.detail


def test_coverage_mismatches_from_statistics_fail() -> None:
    prof, tables, inp = make_world()
    tables["coverage"].stats["mismatches"] = ["Summary/People: says done, item 2 is Not done"]
    r = by_name(run(prof, tables, inp))["coverage_agrees_with_deliverables"]
    assert not r.passed and "says done" in r.detail


def test_coverage_claims_are_compared_when_statistics_are_absent() -> None:
    prof, tables, inp = make_world()
    del tables["coverage"].stats["mismatches"]
    inp.coverage = [CoverageRecord(row_key="people", item="People", number=2)]
    inp.claims = [DeliverableClaim("Summary.docx", "People", "people", says_done=True)]
    r = by_name(run(prof, tables, inp))["coverage_agrees_with_deliverables"]
    assert not r.passed
    inp.claims = [
        DeliverableClaim("Summary.docx", "People", "people", True, carries_partial_note=True)
    ]
    assert by_name(run(prof, tables, inp))["coverage_agrees_with_deliverables"].passed


def test_zip_with_a_missing_readme_path_fails() -> None:
    prof, tables, inp = make_world()
    good = zip_of(prof, tables)
    src = zipfile.ZipFile(io.BytesIO(good))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            if n != "Tables/Job postings.xlsx":
                z.writestr(n, src.read(n))
    r = by_name(run(prof, tables, inp, zip_bytes=out.getvalue()))["zip_contains_readme_paths"]
    assert not r.passed and "missing from archive: Tables/Job postings.xlsx" in r.detail


def test_zip_without_the_jobs_workbook_fails_even_if_the_readme_agrees() -> None:
    prof, tables, inp = make_world()
    smaller = {k: v for k, v in tables.items() if k != "jobs"}
    r = by_name(run(prof, tables, inp, zip_bytes=zip_of(prof, smaller)))[
        "zip_contains_readme_paths"
    ]
    assert not r.passed and "Job postings.xlsx is not in the archive" in r.detail


def test_zip_that_is_not_a_zip_fails() -> None:
    prof, tables, inp = make_world()
    assert not by_name(run(prof, tables, inp, zip_bytes=b"nope"))[
        "zip_contains_readme_paths"
    ].passed


def test_zip_without_readme_fails() -> None:
    prof, tables, inp = make_world()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("Tables/Job postings.xlsx", b"x")
    r = by_name(run(prof, tables, inp, zip_bytes=out.getvalue()))["zip_contains_readme_paths"]
    assert not r.passed and "README.txt is missing" in r.detail


def test_quoted_counts_must_equal_table_counts() -> None:
    prof, tables, inp = make_world()
    inp.quoted_counts["jobs.account_postings"] = 9
    inp.quoted_counts["people.meets_seniority"] = 5
    r = by_name(run(prof, tables, inp))["table_counts_match_deliverables"]
    assert not r.passed and r.numbers["problems"] == 2
    assert "jobs.account_postings: deliverables say 9, table has 2" in r.detail


def test_quoted_counts_with_unknown_keys_are_skipped_with_a_note() -> None:
    prof, tables, inp = make_world()
    inp.quoted_counts = {"nothing.here": 4, "people.total": 2}
    r = by_name(run(prof, tables, inp))["table_counts_match_deliverables"]
    assert (
        r.passed
        and r.numbers == {"compared": 1, "skipped": 1}
        and "nothing.here not compared" in r.detail
    )


def test_checks_skip_cleanly_when_tables_are_absent() -> None:
    prof, _tables, inp = make_world()
    results = run(prof, {}, inp)
    assert C.all_passed(results)
    assert all("skipped" in r.detail or r.numbers for r in results)


def test_refused_news_rows_are_not_rows_from_news() -> None:
    """``dropped_news`` counts what the builder refused: it must not fail the check."""
    prof, tables, inp = make_world()
    tables["people"].stats["dropped_news"] = 22
    assert by_name(run(prof, tables, inp))["people_none_from_news"].passed
    tables["people"].stats["rows_from_news"] = 2
    assert not by_name(run(prof, tables, inp))["people_none_from_news"].passed
