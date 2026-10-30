"""The Sources table, the publisher/type/tier knowledge and citation resolution."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from exports.repair import core
from exports.repair.core import WorkspaceProfile
from exports.repair.records import ArticleRecord, SourceRecord
from exports.repair.sources import (
    build_sources,
    is_job_page,
    publisher_for,
    resolve_citations,
    source_type_for,
    tier_for,
)

RUN = date(2026, 10, 30)
COLLECTED = datetime(2026, 10, 28, 9, 15)


def acct_a() -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name="Zephyrcore Systems",
        account_short="Zephyrcore",
        domain="zephyrcore.example",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
        domains=frozenset({"zephyrcore.example", "zephyr-labs.example"}),
        aliases=("zephyrcore",),
        careers_hosts=frozenset({"talent.zephyrcore-hr.example"}),
        publishers=(("tradeweekly.example", "Trade Weekly (owner override)"),),
    )


def acct_b() -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name="Quillmark Holdings",
        account_short="Quillmark",
        domain="quillmark.test",
        run_date=RUN,
        window_from=date(2025, 10, 30),
        window_to=RUN,
        domains=frozenset({"quillmark.test"}),
        careers_hosts=frozenset({"careers.quillmark-hr.test"}),
    )


def src(code: str, url: str, **kw) -> SourceRecord:
    return SourceRecord(code=code, url=url, **kw)


def sheet(table, name: str = "Sources"):
    return next(s for s in table.sheets if s.name == name).rows


# ---- publisher, type, tier --------------------------------------------------------------------
def test_publisher_generic_table() -> None:
    p = acct_a()
    assert publisher_for("https://www.sec.gov/Archives/x.htm", p).startswith("U.S. Securities")
    assert publisher_for("https://www.reuters.com/a", p) == "Reuters"
    assert publisher_for("https://finance.yahoo.com/q", p) == "Yahoo Finance"
    assert publisher_for("https://www.prnewswire.com/news-releases/x", p) == "PR Newswire"


def test_publisher_own_domains_use_the_account_name() -> None:
    a, b = acct_a(), acct_b()
    assert (
        publisher_for("https://zephyrcore.example/about", a) == "Zephyrcore Systems (company site)"
    )
    assert publisher_for("https://labs.zephyr-labs.example/x", a) == (
        "Zephyrcore Systems (company site)"
    )
    for host in ("news", "investors", "ir", "press"):
        assert publisher_for(f"https://{host}.zephyrcore.example/x", a) == (
            "Zephyrcore Systems Newsroom"
        )
    assert publisher_for("https://quillmark.test/", b) == "Quillmark Holdings (company site)"
    assert publisher_for("https://ir.quillmark.test/", b) == "Quillmark Holdings Newsroom"
    # the same host is not the account's under the other profile
    assert publisher_for("https://zephyrcore.example/about", b) == "Zephyrcore"


def test_publisher_careers_host_override_and_fallback() -> None:
    a = acct_a()
    assert publisher_for("https://talent.zephyrcore-hr.example/job/1", a) == (
        "Zephyrcore Systems (careers site)"
    )
    assert publisher_for("https://www.tradeweekly.example/x", a) == "Trade Weekly (owner override)"
    assert publisher_for("https://www.tradeweekly.example/x", acct_b()) == "Tradeweekly"
    assert publisher_for("https://blog.some-daily-news.co.uk/x", a) == "Some Daily News"
    assert publisher_for("https://example-gazette.test/x", a, given="The Gazette") == "The Gazette"
    assert publisher_for("https://www.sec.gov/x", a, given="whatever") == (
        "whatever"
    )  # a record's own publisher is trusted over the table


def test_a_profile_override_beats_the_record() -> None:
    p = acct_a()
    assert publisher_for("https://tradeweekly.example/a", p, given="TW") == (
        "Trade Weekly (owner override)"
    )


def test_publisher_is_never_empty() -> None:
    for url in ("https://x.example/a", "https://a.b.c.d.example/q", "http://localhost/x"):
        assert publisher_for(url, acct_a()).strip()


def test_source_types() -> None:
    a = acct_a()
    assert source_type_for("https://www.sec.gov/a", a) == "SEC filing"
    assert source_type_for("https://news.zephyrcore.example/a", a) == "Company press release"
    assert source_type_for("https://zephyrcore.example/a", a) == "Company site"
    assert source_type_for("https://zephyrcore.example/careers/x", a) == "Careers site"
    assert source_type_for("https://talent.zephyrcore-hr.example/", a) == "Careers site"
    assert source_type_for("https://www.fiercehealthcare.com/x", a) == "Trade press"
    assert source_type_for("https://seekingalpha.com/x", a) == "Aggregator"
    assert source_type_for("https://www.linkedin.com/company/x", a) == "Profile"
    assert source_type_for("https://builtwith.com/x", a) == "Tech detection"
    assert source_type_for("https://unknown.example/x", a) == "Other"
    assert source_type_for("https://unknown.example/x", a, kind="primary") == "Company site"
    assert source_type_for("https://unknown.example/x", a, kind="aggregator") == "Aggregator"
    assert source_type_for("https://unknown.example/x", a, given="Trade press") == "Trade press"
    assert source_type_for("https://unknown.example/x", a, given="web") == "Other"


def test_tiers() -> None:
    a, b = acct_a(), acct_b()
    assert tier_for("https://zephyrcore.example/a", a) == "primary"
    assert tier_for("https://zephyrcore.example/a", b) == "other"
    assert tier_for("https://www.sec.gov/a", a) == "primary"
    assert tier_for("https://www.reuters.com/a", a) == "trade"
    assert tier_for("https://seekingalpha.com/a", a) == "aggregator"
    # an aggregator is never better than aggregator, whatever a record claims
    assert tier_for("https://seekingalpha.com/a", a, "primary") == "aggregator"
    assert tier_for("https://news.zephyrcore.example/a", a, "aggregator") == "primary"
    assert tier_for("https://unknown.example/a", a, "primary") == "primary"
    assert tier_for("https://unknown.example/a", a, "Trade") == "trade"
    assert tier_for("https://unknown.example/a", a, "nonsense") == "other"
    assert tier_for("https://www.prnewswire.com/a", a) == "trade"
    assert tier_for("https://www.prnewswire.com/a", a, "primary") == "primary"


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.example/job/1234",
        "https://acme.example/jobs/senior-analyst",
        "https://acme.example/careers/open",
        "https://acme.example/en/careers",
        "https://acme-corp.wd5.myworkdayjobs.com/Ext/job/Site/Analyst_R-12345",
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://jobs.lever.co/acme/abc",
        "https://acme.icims.com/jobs/12/x",
        "https://jobs.smartrecruiters.com/Acme/1",
        "https://acme.taleo.net/careersection/x",
        "https://acme.example/requisition/88",
        "https://careers.somewhere.example/",
        "https://jobs.somewhere.example/x",
    ],
)
def test_job_pages_detected(url: str) -> None:
    assert is_job_page(url, acct_a())


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sec.gov/Archives/edgar/data/1/x.htm",
        "https://news.acme.example/2026/10/12/results",
        "https://acme.example/about/leadership",
        "https://www.reuters.com/business/jobs-report",  # 'jobs-report' is not /jobs/
    ],
)
def test_non_job_pages(url: str) -> None:
    assert not is_job_page(url, acct_a())


def test_careers_host_of_the_profile_is_a_job_page() -> None:
    assert is_job_page("https://talent.zephyrcore-hr.example/anything", acct_a())
    assert not is_job_page("https://talent.zephyrcore-hr.example/anything", acct_b())


# ---- the table ---------------------------------------------------------------------------------
def test_basic_table_shape() -> None:
    table = build_sources(
        acct_a(),
        [
            src("S2", "https://www.reuters.com/a", title="Reuters story", collected=COLLECTED,
                published=date(2026, 9, 1), used_in=("Account Summary > Signals",)),
            src("S1", "https://www.sec.gov/f.htm", title="Annual report", collected=COLLECTED,
                published=date(2026, 2, 20), used_in=("Account Summary > Overview", "Sources > A")),
        ],
    )  # fmt: skip
    assert table.key == "sources" and table.title == "Sources"
    assert [s.name for s in table.sheets] == ["Sources", "Job pages"]
    assert all(s.columns == core.SOURCES_COLUMNS for s in table.sheets)
    rows = sheet(table)
    assert [r["Code"] for r in rows] == ["S1", "S2"]
    r1 = rows[0]
    assert r1["Title"] == "Annual report"
    assert r1["Publisher"].startswith("U.S. Securities")
    assert r1["Published"] == "2026-02-20"
    assert r1["Source type"] == "SEC filing"
    assert r1["Link"] == "https://sec.gov/f.htm"
    assert r1["Used in (deliverable + section)"] == "Account Summary > Overview; Sources > A"
    assert r1["Collected"] == "2026-10-28"
    assert all(v.strip() for r in rows for v in r.values())


def test_dedupe_on_canonical_url_prefers_the_coded_row() -> None:
    table = build_sources(
        acct_a(),
        [
            src("Read", "https://www.Reuters.com/a/?utm_source=x", read_only=True,
                title="Page title", used_in=("Deck > Slide 2",)),
            src("S4", "https://reuters.com/a", used_in=("Summary > Intro",)),
        ],
    )  # fmt: skip
    rows = sheet(table)
    assert len(rows) == 1
    assert rows[0]["Code"] == "S4"
    assert rows[0]["Title"] == "Page title"  # the gap is filled from the other row
    assert rows[0]["Used in (deliverable + section)"] == "Summary > Intro; Deck > Slide 2"
    assert table.stats["duplicates_removed"] == 1


def test_two_codes_on_one_url_keep_the_lower_and_still_resolve() -> None:
    table = build_sources(
        acct_a(),
        [src("S9", "https://reuters.com/a"), src("S3", "https://www.reuters.com/a/")],
    )
    rows = sheet(table)
    assert [r["Code"] for r in rows] == ["S3"]
    assert table.stats["code_aliases"] == {"S9": "S3"}
    assert any(e.reason == "duplicate URL merged" and e.key == "S9" for e in table.exclusions)
    assert resolve_citations(["S3", "S9", "S-0009"], table) == []


def test_read_only_pages_keep_the_read_code_and_no_title_is_not_found() -> None:
    table = build_sources(
        acct_a(), [src("", "https://unknown.example/page", read_only=True, collected=COLLECTED)]
    )
    r = sheet(table)[0]
    assert r["Code"] == "Read"
    assert r["Title"] == core.NOT_FOUND
    assert r["Used in (deliverable + section)"] == core.NOT_APPLICABLE
    assert r["Published"] == core.UNKNOWN


def test_title_equal_to_the_url_is_not_a_title() -> None:
    t = build_sources(acct_a(), [src("S1", "https://x.example/a", title="https://x.example/a")])
    assert sheet(t)[0]["Title"] == core.NOT_FOUND


def test_published_never_copies_the_collection_day() -> None:
    table = build_sources(
        acct_a(),
        [
            src("S1", "https://x.example/a", published=date(2026, 10, 28), collected=COLLECTED),
            src("S2", "https://x.example/2026/10/28/b", published=date(2026, 10, 28),
                collected=COLLECTED),
            src("S3", "https://x.example/c", published=date(2026, 10, 28), collected=COLLECTED,
                title="Results announced on October 28, 2026"),
            src("S4", "https://x.example/2026/09/01/d", published=date(2026, 10, 28),
                collected=COLLECTED),
            src("S5", "https://x.example/e", published=date(2026, 10, 27), collected=COLLECTED),
            src("S6", "https://x.example/f", published=None, collected=COLLECTED),
            src("S7", "https://x.example/2026/03/04/g", published=None, collected=COLLECTED),
        ],
    )  # fmt: skip
    got = {r["Code"]: r["Published"] for r in sheet(table)}
    assert got == {
        "S1": core.UNKNOWN,  # unproven collection day
        "S2": "2026-10-28",  # the URL proves it
        "S3": "2026-10-28",  # the title proves it
        "S4": "2026-09-01",  # the URL date replaces the crawl day
        "S5": "2026-10-27",
        "S6": core.UNKNOWN,
        "S7": "2026-03-04",
    }
    assert table.stats["unknown_published"] == 2


def test_job_pages_go_to_their_own_sheet() -> None:
    table = build_sources(
        acct_a(),
        [
            src("S1", "https://www.sec.gov/f.htm"),
            src("Read", "https://acme.wd5.myworkdayjobs.com/Ext/job/Site/Analyst_R-12345"),
            src("", "https://talent.zephyrcore-hr.example/home"),
            src("Read", "https://news.zephyrcore.example/2026/10/12/story"),
        ],
    )
    assert [r["Code"] for r in sheet(table)] == ["S1", "Read"]
    assert {r["Source type"] for r in sheet(table, "Job pages")} == {"Careers site"}
    assert len(sheet(table, "Job pages")) == 2
    assert table.stats["sources"] == 2 and table.stats["job_pages"] == 2


def test_a_cited_source_stays_in_evidence_even_if_it_is_a_job_page() -> None:
    table = build_sources(acct_a(), [src("S5", "https://acme.example/jobs/analyst-1")])
    assert [r["Code"] for r in sheet(table)] == ["S5"]
    assert sheet(table, "Job pages") == []


def test_no_url_is_dropped_and_logged() -> None:
    table = build_sources(
        acct_a(),
        [src("S1", ""), src("S2", "   "), src("S3", "mailto:someone@x.example"),
         src("S4", "not a url"), src("S5", "https://ok.example/a")],
    )  # fmt: skip
    assert [r["Code"] for r in sheet(table)] == ["S5"]
    reasons = {e.key: e.reason for e in table.exclusions}
    assert reasons["S1"] == "no source URL" and reasons["S2"] == "no source URL"
    assert reasons["S3"] == "not a web address" and reasons["S4"] == "not a web address"
    assert table.stats["dropped"] == 4


def test_collected_missing_is_marked() -> None:
    r = sheet(build_sources(acct_a(), [src("S1", "https://x.example/a")]))[0]
    assert r["Collected"] == "Not collected: collection time not recorded"
    assert core.is_placeholder(r["Collected"])


def test_articles_without_a_register_row_become_read_rows() -> None:
    art = ArticleRecord(
        url="https://www.reuters.com/z", title="Story", published=date(2026, 8, 1),
        published_kind="page", collected=COLLECTED,
    )  # fmt: skip
    coded = ArticleRecord(url="https://www.reuters.com/y", title="Coded", source_code="S-0007")
    listed = ArticleRecord(url="https://www.sec.gov/f.htm", title="ignored")
    table = build_sources(
        acct_a(), [src("S1", "https://www.sec.gov/f.htm", title="Filing")], [art, coded, listed]
    )
    by_link = {r["Link"]: r for r in sheet(table)}
    assert by_link["https://reuters.com/z"]["Code"] == "Read"
    assert by_link["https://reuters.com/z"]["Published"] == "2026-08-01"
    assert by_link["https://reuters.com/y"]["Code"] == "S-0007"
    assert by_link["https://sec.gov/f.htm"]["Title"] == "Filing"
    assert len(by_link) == 3


def test_publisher_and_type_are_never_empty() -> None:
    table = build_sources(
        acct_a(), [src("S1", "https://a.example/x"), src("", "https://zephyrcore.example/x")]
    )
    for r in sheet(table):
        assert r["Publisher"].strip() and r["Source type"].strip()


# ---- citations ---------------------------------------------------------------------------------
def test_resolve_citations_normalises_forms() -> None:
    table = build_sources(
        acct_a(), [src("S1", "https://a.example/1"), src("S-0002", "https://a.example/2")]
    )
    assert resolve_citations(["S1", "S-0001", "s2", "S-0002", "S02"], table) == []
    assert resolve_citations(["S3", "S-0010", "S10", "S2", ""], table) == ["S3", "S10"]
    assert resolve_citations([], table) == []


def test_resolve_citations_search_every_sheet() -> None:
    table = build_sources(
        acct_a(), [src("S1", "https://a.example/1"), src("Read", "https://acme.example/jobs/x")]
    )
    assert resolve_citations(["S1", "Read"], table) == []


# ---- neutrality and idempotence ----------------------------------------------------------------
def test_two_accounts_same_records_differ_only_by_their_own_domains() -> None:
    records = [
        src("S1", "https://www.sec.gov/f.htm", title="Filing"),
        src("S2", "https://www.reuters.com/a", title="Story"),
    ]
    a = build_sources(acct_a(), records)
    b = build_sources(acct_b(), records)
    assert sheet(a) == sheet(b)


def test_each_account_labels_its_own_site_only() -> None:
    records = [
        src("S1", "https://news.zephyrcore.example/x", title="Release A"),
        src("S2", "https://news.quillmark.test/x", title="Release B"),
    ]
    a = {r["Code"]: r for r in sheet(build_sources(acct_a(), records))}
    b = {r["Code"]: r for r in sheet(build_sources(acct_b(), records))}
    assert a["S1"]["Publisher"] == "Zephyrcore Systems Newsroom"
    assert a["S2"]["Publisher"] == "Quillmark"
    assert b["S2"]["Publisher"] == "Quillmark Holdings Newsroom"
    assert b["S1"]["Publisher"] == "Zephyrcore"


def test_idempotent_and_order_independent() -> None:
    records = [
        src("S2", "https://b.example/2", title="B", published=date(2026, 1, 2)),
        src("S1", "https://a.example/1", title="A"),
        src("Read", "https://c.example/3"),
        src("S1", "https://a.example/1/", title="A longer"),
    ]
    one = build_sources(acct_a(), records)
    two = build_sources(acct_a(), list(reversed(records)))
    assert one.sheets[0].rows == build_sources(acct_a(), records).sheets[0].rows
    assert [r["Code"] for r in sheet(one)] == [r["Code"] for r in sheet(two)]
    # the built rows, read back as records, give the same table
    back = [
        src(r["Code"], r["Link"], title="" if r["Title"] == core.NOT_FOUND else r["Title"])
        for r in sheet(one)
    ]
    again = build_sources(acct_a(), back)
    assert [(r["Code"], r["Link"]) for r in sheet(again)] == [
        (r["Code"], r["Link"]) for r in sheet(one)
    ]
