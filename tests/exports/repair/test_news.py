"""The News table: one row per canonical URL, real headlines and dates, grades by source."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from exports.repair import core
from exports.repair.core import WorkspaceProfile
from exports.repair.news import build_news, is_fragment_title, regrade
from exports.repair.records import ArticleRecord, FactLine

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
        domains=frozenset({"zephyrcore.example"}),
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
    )


def art(url: str, title: str = "A proper headline about results", **kw) -> ArticleRecord:
    kw.setdefault("collected", COLLECTED)
    return ArticleRecord(url=url, title=title, **kw)


def fact(text: str, grade: str = "confirmed", fid: str = "", **kw) -> FactLine:
    return FactLine(fact_id=fid or text[:6], text=text, grade=grade, **kw)


def news(table):
    return table.sheets[0].rows


def facts(table):
    return table.sheets[1].rows


# ---- shape -------------------------------------------------------------------------------------
def test_shape_and_columns() -> None:
    item = art(
        "https://www.reuters.com/a",
        published=date(2026, 9, 1),
        published_kind="page",
        facts=(fact("It grew."),),
    )
    table = build_news(acct_a(), [item])
    assert table.key == "news" and table.title == "News"
    assert [s.name for s in table.sheets] == ["News", "News facts"]
    assert table.sheets[0].columns == core.NEWS_COLUMNS
    assert table.sheets[1].columns == core.NEWS_FACT_COLUMNS
    row = news(table)[0]
    assert row["Article ID"] == "A-0001"
    assert row["Headline"] == "A proper headline about results"
    assert row["Published"] == "2026-09-01"
    assert row["Collected"] == "2026-10-28"
    assert row["Within window (Y/N)"] == "Y"
    assert row["Superseded by"] == core.NOT_APPLICABLE
    assert row["Publisher"] == "Reuters"
    assert row["Source type"] == "Trade press"
    assert row["Source URL"] == "https://reuters.com/a"
    assert all(v.strip() for v in row.values())


# ---- headline ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        "and the board approved the plan",  # starts lower-case mid-sentence
        "“We are growing fast and will keep investing,”",  # a quote
        '"We expect growth in every region"',
        "x" * 150,  # long, no closing punctuation
    ],
)
def test_fragment_titles(title: str) -> None:
    assert is_fragment_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "Zephyrcore names new chief executive",
        "iPhone-maker expands its plant",  # a camel-cased brand may start lower-case
        "Q3 results: revenue up 4%",
        ("A long headline " * 12).strip() + ".",  # long but ends with a full stop
        '"Growth" is the word at Zephyrcore',
    ],
)
def test_real_titles(title: str) -> None:
    assert not is_fragment_title(title)


def test_title_equal_to_a_fact_is_a_fragment() -> None:
    assert is_fragment_title("The company will open a plant.", ["the company  will open a plant."])
    assert not is_fragment_title("The company will open a plant.", ["Something else"])


def test_fragment_headline_becomes_not_found_and_logs_nothing() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", title="and then it rained", facts=(fact("It rained."),)),
            art(
                "https://x.example/b",
                title="It rained on Monday",
                facts=(fact("It rained on Monday"),),
            ),
            art("https://x.example/c", title=""),
        ],
    )
    assert {r["Headline"] for r in news(table)} == {core.NOT_FOUND}
    assert table.exclusions == []
    assert table.stats["headlines_not_found"] == 3


def test_best_title_is_taken_from_the_duplicates() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", title="short"),  # fragment
            art("https://x.example/a/", title="Zephyrcore opens plant"),
            art("https://www.x.example/a?utm_source=t", title="Zephyrcore opens plant in Ohio"),
        ],
    )
    assert len(news(table)) == 1
    assert news(table)[0]["Headline"] == "Zephyrcore opens plant in Ohio"


# ---- merging and ids ---------------------------------------------------------------------------
def test_one_row_per_canonical_url_and_facts_unite() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", facts=(fact("Fact one."), fact("Fact two.", "probable"))),
            art(
                "https://X.example/a/?utm_medium=e", facts=(fact("fact  one."), fact("Fact three."))
            ),
            art("https://x.example/b"),
        ],
    )
    assert len(news(table)) == 2
    assert table.stats["duplicates_removed"] == 1
    assert table.stats["articles"] == 2 and table.stats["facts"] == 3
    a_id = table.stats["article_ids"]["https://x.example/a"]
    on_a = [f for f in facts(table) if f["Article ID"] == a_id]
    assert sorted(f["Fact"] for f in on_a) == ["Fact one.", "Fact three.", "Fact two."]
    assert facts(table) == on_a


def test_ids_follow_published_desc_unknown_last_then_url() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/undated-z"),
            art("https://x.example/old", published=date(2026, 1, 5), published_kind="page"),
            art("https://x.example/undated-a"),
            art("https://x.example/new", published=date(2026, 10, 1), published_kind="page"),
        ],
    )
    order = [(r["Article ID"], r["Source URL"].rsplit("/", 1)[1]) for r in news(table)]
    assert order == [
        ("A-0001", "new"), ("A-0002", "old"), ("A-0003", "undated-a"), ("A-0004", "undated-z"),
    ]  # fmt: skip
    assert table.stats["article_ids"] == {
        "https://x.example/new": "A-0001",
        "https://x.example/old": "A-0002",
        "https://x.example/undated-a": "A-0003",
        "https://x.example/undated-z": "A-0004",
    }


def test_input_order_does_not_change_ids() -> None:
    items = [
        art("https://x.example/a", published=date(2026, 3, 1), published_kind="url"),
        art("https://x.example/b", published=date(2026, 6, 1), published_kind="page"),
        art("https://x.example/c"),
    ]
    one = build_news(acct_a(), items)
    two = build_news(acct_a(), list(reversed(items)))
    assert one.stats["article_ids"] == two.stats["article_ids"]
    assert news(one) == news(two)


# ---- dates -------------------------------------------------------------------------------------
def test_published_needs_a_proven_kind() -> None:
    d = date(2026, 9, 9)
    cases = {
        "page": "2026-09-09", "url": "2026-09-09", "filing": "2026-09-09",
        "collected": core.UNKNOWN, "": core.UNKNOWN, "guess": core.UNKNOWN,
    }  # fmt: skip
    for kind, expected in cases.items():
        table = build_news(acct_a(), [art("https://x.example/a", published=d, published_kind=kind)])
        assert news(table)[0]["Published"] == expected, kind


def test_collection_day_is_unknown_unless_the_source_proves_it() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", published=date(2026, 10, 28), published_kind="collected"),
            art("https://x.example/b", published=date(2026, 10, 28), published_kind=""),
            art("https://x.example/c", published=date(2026, 10, 28), published_kind="page"),
            art("https://x.example/2026/10/28/d", published=date(2026, 10, 28),
                published_kind="collected"),
        ],
    )  # fmt: skip
    got = {r["Source URL"].rsplit("/", 1)[1]: r["Published"] for r in news(table)}
    assert got == {"a": core.UNKNOWN, "b": core.UNKNOWN, "c": "2026-10-28", "d": "2026-10-28"}
    assert table.stats["unknown_dates"] == 2


def test_date_read_from_url_or_title() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/2026/08/03/story"),
            art("https://x.example/b", title="Board meets on July 4, 2026 to vote"),
            art("https://x.example/c", title="Plain headline"),
        ],
    )
    got = {r["Source URL"].rsplit("/", 1)[-1]: r["Published"] for r in news(table)}
    assert got["story"] == "2026-08-03"
    assert got["b"] == "2026-07-04"
    assert got["c"] == core.UNKNOWN


def test_a_url_id_is_not_a_date() -> None:
    row = news(build_news(acct_a(), [art("https://x.example/26/10/62156538/story")]))[0]
    assert row["Published"] == core.UNKNOWN


def test_window() -> None:
    p = acct_a()
    table = build_news(
        p,
        [
            art("https://x.example/in", published=date(2026, 1, 1), published_kind="page"),
            art("https://x.example/edge1", published=p.window_from, published_kind="page"),
            art("https://x.example/edge2", published=p.window_to, published_kind="page"),
            art("https://x.example/out", published=date(2025, 10, 29), published_kind="page"),
            art("https://x.example/future", published=date(2026, 10, 31), published_kind="page"),
            art("https://x.example/none"),
        ],
    )
    got = {r["Source URL"].rsplit("/", 1)[-1]: r["Within window (Y/N)"] for r in news(table)}
    assert got == {"in": "Y", "edge1": "Y", "edge2": "Y", "out": "N", "future": "N",
                   "none": core.UNKNOWN}  # fmt: skip
    assert table.stats["outside_window"] == 2


def test_collected_marker() -> None:
    row = news(build_news(acct_a(), [art("https://x.example/a", collected=None)]))[0]
    assert row["Collected"] == "Not collected: collection time not recorded"


# ---- grading -----------------------------------------------------------------------------------
def test_regrade_matrix() -> None:
    assert regrade("confirmed", "primary") == "Confirmed"
    assert regrade("confirmed", "trade") == "Probable"
    assert regrade("confirmed", "aggregator") == "Lead"
    assert regrade("confirmed", "other") == "Lead"
    assert regrade("probable", "primary") == "Probable"  # never above the stored grade
    assert regrade("Probable", "trade") == "Probable"
    assert regrade("hypothesis", "primary") == "Lead"
    assert regrade("lead", "primary") == "Lead"
    assert regrade("", "primary") == "Lead"
    assert regrade("weird", "primary") == "Lead"


def test_grades_follow_the_source() -> None:
    stored = (
        fact("Fact A.", "confirmed"),
        fact("Fact B.", "probable"),
        fact("Fact C.", "hypothesis"),
    )
    cases = {
        "https://zephyrcore.example/about": ("Confirmed", ["Confirmed", "Probable", "Lead"]),
        "https://news.zephyrcore.example/2026/10/01/x": ("Confirmed", None),
        "https://www.sec.gov/Archives/f.htm": ("Confirmed", None),
        "https://www.fiercehealthcare.com/x": ("Probable", ["Probable", "Probable", "Lead"]),
        "https://seekingalpha.com/article/1-transcript": ("Lead", ["Lead", "Lead", "Lead"]),
        "https://unknown-blog.example/post": ("Lead", ["Lead", "Lead", "Lead"]),
    }
    for url, (best, per_fact) in cases.items():
        table = build_news(acct_a(), [art(url, facts=stored)])
        assert news(table)[0]["Fact grade"] == best, url
        if per_fact:
            assert [f["Fact grade"] for f in facts(table)] == sorted(
                per_fact, key={"Confirmed": 0, "Probable": 1, "Lead": 2}.get
            ), url


def test_record_tier_primary_counts_but_an_aggregator_host_never_does() -> None:
    f = (fact("A fact.", "confirmed"),)
    wire = build_news(acct_a(), [art("https://www.businesswire.com/x", tier="primary", facts=f)])
    assert news(wire)[0]["Fact grade"] == "Confirmed"
    wire_untiered = build_news(acct_a(), [art("https://www.businesswire.com/x", facts=f)])
    assert news(wire_untiered)[0]["Fact grade"] == "Probable"
    agg = build_news(acct_a(), [art("https://seekingalpha.com/a", tier="primary", facts=f)])
    assert news(agg)[0]["Fact grade"] == "Lead"


def test_own_domain_is_account_specific() -> None:
    f = (fact("A fact.", "confirmed"),)
    url = "https://zephyrcore.example/ir/news"
    assert news(build_news(acct_a(), [art(url, facts=f)]))[0]["Fact grade"] == "Confirmed"
    assert news(build_news(acct_b(), [art(url, facts=f)]))[0]["Fact grade"] == "Lead"


def test_article_without_facts_is_lead_and_has_no_fact_rows() -> None:
    table = build_news(acct_a(), [art("https://zephyrcore.example/a")])
    assert news(table)[0]["Fact grade"] == "Lead"
    assert facts(table) == []


def test_grade_counts() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://zephyrcore.example/a", facts=(fact("One."), fact("Two.", "probable"))),
            art("https://www.reuters.com/b", facts=(fact("Three."),)),
            art("https://blog.example/c", facts=(fact("Four."),)),
        ],
    )
    assert table.stats["graded"] == {"Confirmed": 1, "Probable": 1, "Lead": 1}
    assert table.stats["fact_grades"] == {"Confirmed": 1, "Probable": 2, "Lead": 1}


# ---- facts sheet -------------------------------------------------------------------------------
def test_fact_rows_dedupe_and_quote() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://zephyrcore.example/a", facts=(
                fact("Revenue rose 4%.", "confirmed", "f1", quote="Revenue rose four percent."),
                fact("revenue  rose 4%.", "probable", "f2"),
                fact("No quote here.", "confirmed", "f3"),
                fact("   ", "confirmed", "f4"),
            )),
        ],
    )  # fmt: skip
    rows = facts(table)
    assert len(rows) == 2
    assert {r["Fact"] for r in rows} == {"Revenue rose 4%.", "No quote here."}
    by_fact = {r["Fact"]: r for r in rows}
    assert by_fact["Revenue rose 4%."]["Quote"] == "Revenue rose four percent."
    assert by_fact["No quote here."]["Quote"] == core.NOT_FOUND
    assert by_fact["Revenue rose 4%."]["Fact grade"] == "Confirmed"  # the best duplicate counts
    assert all(r["Article ID"] == "A-0001" for r in rows)
    assert all(v.strip() for r in rows for v in r.values())


# ---- supersession ------------------------------------------------------------------------------
def test_superseded_by_points_to_the_newer_article() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/old", published=date(2026, 3, 1), published_kind="page",
                facts=(fact("The CFO is Ann Reed.", fid="f-old", superseded_by_fact="f-new"),)),
            art("https://x.example/new", published=date(2026, 8, 1), published_kind="page",
                facts=(fact("The CFO is Bo Lin.", fid="f-new"),)),
            art("https://x.example/other", published=date(2026, 5, 1), published_kind="page"),
        ],
    )  # fmt: skip
    got = {r["Source URL"].rsplit("/", 1)[1]: r["Superseded by"] for r in news(table)}
    ids = table.stats["article_ids"]
    assert got["old"] == ids["https://x.example/new"] == "A-0001"
    assert got["new"] == core.NOT_APPLICABLE
    assert got["other"] == core.NOT_APPLICABLE


def test_superseded_by_ignores_self_unknown_ids_and_older_articles() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", published=date(2026, 3, 1), published_kind="page",
                facts=(fact("A.", fid="fa", superseded_by_fact="fa"),
                       fact("B.", fid="fb", superseded_by_fact="missing"))),
            # points at a fact of an OLDER article: not "newer", so no link
            art("https://x.example/b", published=date(2026, 8, 1), published_kind="page",
                facts=(fact("C.", fid="fc", superseded_by_fact="fa"),)),
        ],
    )  # fmt: skip
    assert {r["Superseded by"] for r in news(table)} == {core.NOT_APPLICABLE}


def test_mutual_supersession_links_one_way_only() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/p", facts=(fact("P.", fid="fp", superseded_by_fact="fq"),)),
            art("https://x.example/q", facts=(fact("Q.", fid="fq", superseded_by_fact="fp"),)),
        ],
    )
    got = [r["Superseded by"] for r in news(table)]
    assert got == [core.NOT_APPLICABLE, "A-0001"]


# ---- publisher and source type ----------------------------------------------------------------
def test_publisher_and_type_for_each_account() -> None:
    urls = ["https://news.zephyrcore.example/x", "https://news.quillmark.test/x"]
    a = {r["Source URL"]: r for r in news(build_news(acct_a(), [art(u) for u in urls]))}
    b = {r["Source URL"]: r for r in news(build_news(acct_b(), [art(u) for u in urls]))}
    assert a[urls[0]]["Publisher"] == "Zephyrcore Systems Newsroom"
    assert a[urls[0]]["Source type"] == "Company press release"
    assert b[urls[1]]["Publisher"] == "Quillmark Holdings Newsroom"
    assert b[urls[0]]["Publisher"] == "Zephyrcore"
    assert b[urls[0]]["Source type"] == "Other"


def test_record_publisher_is_used_for_unknown_hosts() -> None:
    row = news(
        build_news(acct_a(), [art("https://odd-daily.example/a", publisher="The Odd Daily")])
    )[0]
    assert row["Publisher"] == "The Odd Daily"
    row = news(build_news(acct_a(), [art("https://odd-daily.example/a")]))[0]
    assert row["Publisher"] == "Odd Daily"


# ---- drops -------------------------------------------------------------------------------------
def test_articles_without_url_are_dropped_and_logged() -> None:
    table = build_news(
        acct_a(),
        [art("", title="Orphan headline"), art("   "), art("https://x.example/a")],
    )
    assert len(news(table)) == 1
    assert [(e.table, e.reason) for e in table.exclusions] == [("news", "no source URL")] * 2
    assert table.exclusions[0].key == "Orphan headline"
    assert table.stats["dropped"] == 2


# ---- neutrality and idempotence ----------------------------------------------------------------
def test_two_accounts_identical_for_neutral_sources() -> None:
    items = [
        art("https://www.reuters.com/a", published=date(2026, 6, 1), published_kind="page",
            facts=(fact("It grew."),)),
        art("https://www.sec.gov/f.htm", published=date(2026, 2, 1), published_kind="filing",
            facts=(fact("Filed."),)),
    ]  # fmt: skip
    a = build_news(acct_a(), items)
    b = build_news(acct_b(), items)
    assert news(a) == news(b) and facts(a) == facts(b)
    assert a.stats == b.stats


def test_idempotent() -> None:
    items = [
        art("https://zephyrcore.example/a", published=date(2026, 6, 1), published_kind="page",
            facts=(fact("One."), fact("Two.", "probable", quote="two"))),
        art("https://seekingalpha.com/x", facts=(fact("Three."),)),
        art("https://zephyrcore.example/a/", title="A second, longer headline for the page"),
    ]  # fmt: skip
    one, two = build_news(acct_a(), items), build_news(acct_a(), items)
    assert news(one) == news(two) and facts(one) == facts(two) and one.stats == two.stats


def test_no_cell_is_blank() -> None:
    table = build_news(
        acct_a(),
        [
            art("https://x.example/a", title="", collected=None, facts=(fact("A fact."),)),
            art("https://zephyrcore.example/b", facts=(fact("B fact.", quote="b"),)),
        ],
    )
    for sheet in table.sheets:
        for row in sheet.rows:
            assert set(row) == set(sheet.columns)
            assert all(v.strip() for v in row.values())


def test_empty_input() -> None:
    table = build_news(acct_a(), [])
    assert news(table) == [] and facts(table) == []
    assert table.stats["articles"] == 0 and table.stats["article_ids"] == {}
