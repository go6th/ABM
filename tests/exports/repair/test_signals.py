"""Signals table: generic detection on two invented accounts, sources required, no clock."""

from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any

import pytest

from exports.repair import core
from exports.repair.core import EntityRule, RepairedTable, Sheet, WorkspaceProfile
from exports.repair.records import SignalSeed
from exports.repair.signals import (
    build_signals,
    classify_text,
    map_signal_type,
    normalise_grade,
    parse_day,
)

# two unrelated invented accounts: nothing in the code may depend on either
ACCOUNT_A = {
    "account_name": "Quillfern Holdings",
    "account_short": "Quillfern",
    "domain": "quillfern.example",
    "lanes": ("Alpha CRM", "Beta ERP"),
    "run_date": date(2026, 10, 31),
    "window_from": date(2025, 11, 1),
    "window_to": date(2026, 10, 31),
    "other": "Zephyr Unit",
}
ACCOUNT_B = {
    "account_name": "Marlow Tideworks",
    "account_short": "Marlow",
    "domain": "marlow.test",
    "lanes": ("Gamma BI", "Delta HR"),
    "run_date": date(2027, 3, 15),
    "window_from": date(2026, 3, 16),
    "window_to": date(2027, 3, 15),
    "other": "Orchid Works",
}


def make_profile(acc: dict[str, Any]) -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name=acc["account_name"],
        account_short=acc["account_short"],
        domain=acc["domain"],
        run_date=acc["run_date"],
        window_from=acc["window_from"],
        window_to=acc["window_to"],
        lanes=acc["lanes"],
        entities=(EntityRule(acc["other"], (acc["other"],), counts_as_account=False,
                             before="2026-01-15"),),
    )  # fmt: skip


def jobs_row(
    profile: WorkspaceProfile, n: int, platform: str, day: date | None, **over: str
) -> dict[str, str]:
    row = {c: core.NOT_FOUND for c in core.JOBS_COLUMNS}
    row.update(
        {
            "Posting ID": f"P{n}",
            "Title": f"Engineer {n}",
            "Entity": profile.label,
            "Platforms named": platform,
            "Lane(s)": platform,
            "Class": "Working tool",
            "Status": "Open",
            "Posted": day.isoformat() if day else core.UNKNOWN,
            "First seen": core.UNKNOWN,
            "Platform sentences": "Experience with the platform.",
            "Source URL": f"https://careers.{profile.domain}/jobs/{n}",
        }
    )
    row.update(over)
    return row


def jobs_table(rows: list[dict[str, str]], per_platform: dict | None = None) -> RepairedTable:
    return RepairedTable(
        "jobs", "Job postings", [Sheet("Job postings", core.JOBS_COLUMNS, rows)],
        stats={"per_platform": per_platform} if per_platform is not None else {},
    )  # fmt: skip


def news_table(
    articles: list[dict[str, str]], facts: list[dict[str, str]] | None = None
) -> RepairedTable:
    news = Sheet("News", core.NEWS_COLUMNS, [_full(core.NEWS_COLUMNS, a) for a in articles])
    fact_sheet = Sheet(
        "News facts",
        core.NEWS_FACT_COLUMNS,
        [_full(core.NEWS_FACT_COLUMNS, f) for f in facts or []],
    )
    return RepairedTable("news", "News", [news, fact_sheet])


def _full(columns: tuple[str, ...], row: dict[str, str]) -> dict[str, str]:
    return {c: row.get(c, core.NOT_FOUND) for c in columns}


def sources_table(pairs: dict[str, str]) -> RepairedTable:
    rows = [_full(core.SOURCES_COLUMNS, {"Code": c, "Link": u}) for c, u in pairs.items()]
    return RepairedTable("sources", "Sources", [Sheet("Sources", core.SOURCES_COLUMNS, rows)])


def leadership_table(rows: list[dict[str, str]]) -> RepairedTable:
    people = Sheet("People", core.PEOPLE_COLUMNS, [])
    lead = Sheet(
        "Leadership", core.LEADERSHIP_COLUMNS, [_full(core.LEADERSHIP_COLUMNS, r) for r in rows]
    )
    return RepairedTable("people", "People", [people, lead])


def ranking_table(lanes: dict[str, str]) -> RepairedTable:
    rows = [_full(core.RANKING_COLUMNS, {"Platform": p, "Lane": lane}) for p, lane in lanes.items()]
    return RepairedTable("tech", "Tech", [Sheet("Platform Ranking", core.RANKING_COLUMNS, rows)])


def by_type(table: RepairedTable, kind: str) -> list[dict[str, str]]:
    return [r for r in table.main.rows if r["Type"] == kind]


@pytest.fixture(params=[ACCOUNT_A, ACCOUNT_B], ids=["account_a", "account_b"])
def acc(request: pytest.FixtureRequest) -> dict[str, Any]:
    return request.param


# ---- hiring signals -----------------------------------------------------------------------------
def spike_rows(profile: WorkspaceProfile, platform: str, recent: int, older: int) -> list:
    end = profile.window_to
    rows = [jobs_row(profile, i, platform, end - timedelta(days=i * 3)) for i in range(recent)]
    rows += [
        jobs_row(profile, 100 + i, platform, profile.window_from + timedelta(days=20 + i * 15))
        for i in range(older)
    ]
    return rows


def test_platform_hiring_spike_detected_and_confirmed(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    platform = acc["lanes"][0]
    table = build_signals(profile, {"jobs": jobs_table(spike_rows(profile, platform, 6, 2))})
    [sig] = by_type(table, "Platform hiring spike")
    assert platform in sig["Signal"] and profile.label in sig["Signal"]
    assert sig["Fact grade"] == "Confirmed"
    assert sig["Detected"] == profile.run_date.isoformat()
    assert sig["Event date"] == profile.window_to.isoformat()
    assert sig["Evidence (source codes)"].startswith("https://careers.")
    assert sig["Lane"] == platform  # one lane on every posting
    assert table.stats["per_type"]["Platform hiring spike"] == 1


def test_no_spike_when_recent_is_not_double_the_average(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    platform = acc["lanes"][0]
    # evenly spread: one posting every ~9 days across the whole window
    rows = [
        jobs_row(profile, i, platform, profile.window_from + timedelta(days=i * 9))
        for i in range(40)
    ]
    table = build_signals(profile, {"jobs": jobs_table(rows)})
    assert by_type(table, "Platform hiring spike") == []


def test_no_spike_below_five_recent_postings(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    table = build_signals(profile, {"jobs": jobs_table(spike_rows(profile, acc["lanes"][0], 4, 0))})
    assert by_type(table, "Platform hiring spike") == []


def test_spike_ignores_other_entities_and_outside_window(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    platform = acc["lanes"][0]
    rows = spike_rows(profile, platform, 6, 0)
    for r in rows:
        r["Entity"] = acc["other"]
    rows += [
        jobs_row(profile, 500 + i, platform, profile.window_from - timedelta(days=10 + i))
        for i in range(8)
    ]
    table = build_signals(profile, {"jobs": jobs_table(rows)})
    assert by_type(table, "Platform hiring spike") == []


def test_spike_with_sources_register_uses_codes(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    rows = spike_rows(profile, acc["lanes"][0], 5, 0)
    sources = sources_table({"S7": rows[0]["Source URL"] + "/"})  # canonical match, trailing slash
    table = build_signals(profile, {"jobs": jobs_table(rows), "sources": sources})
    [sig] = by_type(table, "Platform hiring spike")
    assert sig["Evidence (source codes)"].split("; ")[0] == "S7"


def test_build_role_grade_depends_on_posting_count(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    p1, p2 = acc["lanes"]
    rows = [
        jobs_row(profile, i, p1, profile.window_from + timedelta(days=30 + i), Class="Build role")
        for i in range(3)
    ] + [jobs_row(profile, 50, p2, profile.window_from + timedelta(days=90), Class="Build role")]
    stats = {
        p1: {"postings": 3, "open": 3, "build": 3, "admin": 0, "tool": 0, "user": 0},
        p2: {"postings": 1, "open": 1, "build": 1, "admin": 0, "tool": 0, "user": 0},
    }
    table = build_signals(profile, {"jobs": jobs_table(rows, stats)})
    signals = {
        s["Signal"].split(" naming ")[1].rstrip("."): s for s in by_type(table, "Build-role hiring")
    }
    assert signals[p1]["Fact grade"] == "Confirmed"
    assert signals[p2]["Fact grade"] == "Probable"
    assert "3 in-window build-role postings" in signals[p1]["Signal"]
    assert "1 in-window build-role posting " in signals[p2]["Signal"]


def test_build_role_zero_build_makes_no_signal(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    rows = [jobs_row(profile, 1, acc["lanes"][0], date(2026, 2, 1))]
    stats = {acc["lanes"][0]: {"postings": 1, "build": 0}}
    table = build_signals(profile, {"jobs": jobs_table(rows, stats)})
    assert by_type(table, "Build-role hiring") == []


def test_build_role_count_without_any_source_is_dropped_and_logged(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    platform = acc["lanes"][0]
    stats = {platform: {"postings": 4, "build": 4}}
    table = build_signals(profile, {"jobs": jobs_table([], stats)})
    assert table.main.rows == []
    assert table.stats["dropped"] == 1
    [ex] = table.exclusions
    assert (ex.table, ex.reason) == ("signals", "no source")


def test_lane_comes_from_platform_ranking(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    rows = [jobs_row(profile, 1, "Mystery Tool", date(2026, 4, 4), Class="Build role",
                     **{"Lane(s)": "A; B"})]  # fmt: skip
    stats = {"Mystery Tool": {"postings": 1, "build": 1}}
    tables = {
        "jobs": jobs_table(rows, stats),
        "tech": ranking_table({"Mystery Tool": acc["lanes"][1]}),
    }
    [sig] = by_type(build_signals(profile, tables), "Build-role hiring")
    assert sig["Lane"] == acc["lanes"][1]
    # without the ranking and with two lanes on the postings: not applicable
    [sig2] = by_type(build_signals(profile, {"jobs": tables["jobs"]}), "Build-role hiring")
    assert sig2["Lane"] == core.NOT_APPLICABLE


def test_program_wording_in_postings_needs_two(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    platform = acc["lanes"][0]
    text = "Lead the migration of our estate."
    one = [jobs_row(profile, 1, platform, date(2026, 5, 5), **{"Platform sentences": text})]
    assert by_type(build_signals(profile, {"jobs": jobs_table(one)}), "Program or migration") == []
    two = one + [jobs_row(profile, 2, platform, date(2026, 6, 5), Title="Rollout lead")]
    [sig] = by_type(build_signals(profile, {"jobs": jobs_table(two)}), "Program or migration")
    assert sig["Fact grade"] == "Probable"
    assert platform in sig["Signal"]


def test_divested_entity_rows_make_an_ma_signal(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    rows = [
        jobs_row(profile, i, acc["lanes"][0], date(2026, 3, 3), Entity=acc["other"])
        for i in range(3)
    ]
    [sig] = by_type(build_signals(profile, {"jobs": jobs_table(rows)}), "M&A / divestiture")
    assert acc["other"] in sig["Signal"] and "3 job postings" in sig["Signal"]
    assert sig["Event date"] == "2026-01-15"
    assert sig["Fact grade"] == "Probable"


# ---- news ---------------------------------------------------------------------------------------
NEWS_CASES = [
    ("The board appoints a new Chief Revenue Officer.", "Leadership change"),
    ("{co} signed a definitive agreement to divest its diagnostics unit.", "M&A / divestiture"),
    ("The company received an FDA warning letter after an inspection.", "Regulatory event"),
    ("{co} priced a senior notes offering of 500 million.", "Funding / financial"),
    ("The harmonization of finance systems continues through next year.", "Program or migration"),
]


@pytest.mark.parametrize(("fact", "kind"), NEWS_CASES)
def test_news_fact_types(acc: dict[str, Any], fact: str, kind: str) -> None:
    profile = make_profile(acc)
    url = f"https://news.example.org/{abs(hash(kind)) % 999}"
    news = news_table(
        [{"Article ID": "A1", "Headline": "Some headline", "Published": "2026-06-01",
          "Source URL": url, "Fact grade": "Probable"}],
        [{"Article ID": "A1", "Fact": fact.format(co=profile.label), "Fact grade": "Confirmed"}],
    )  # fmt: skip
    table = build_signals(profile, {"news": news})
    [sig] = table.main.rows
    assert sig["Type"] == kind
    assert sig["Fact grade"] == "Confirmed"  # the fact's own grade wins
    assert sig["Event date"] == "2026-06-01"
    assert sig["Evidence (source codes)"] == url
    assert sig["Signal"].endswith(".")
    assert sig["Lane"] == core.NOT_APPLICABLE


def test_news_signal_uses_source_code_and_unknown_event_date(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    url = "https://trade.example.org/story?utm_source=x"
    news = news_table(
        [{"Article ID": "A1", "Published": core.UNKNOWN, "Source URL": url, "Fact grade": "Lead"}],
        [{"Article ID": "A1", "Fact": "Company announces a recall of two products"}],
    )
    sources = sources_table({"S3": "https://trade.example.org/story"})
    [sig] = build_signals(profile, {"news": news, "sources": sources}).main.rows
    assert sig["Evidence (source codes)"] == "S3"
    assert sig["Event date"] == core.UNKNOWN
    assert sig["Fact grade"] == "Lead"  # a blank fact grade falls back to the article grade


def test_headline_is_used_only_when_an_article_has_no_facts(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    news = news_table(
        [
            {"Article ID": "A1", "Headline": "Firm to acquire a rival", "Published": "2026-05-05",
             "Source URL": "https://news.example.org/a1", "Fact grade": "Probable"},
            {"Article ID": "A2", "Headline": "Firm to acquire a supplier",
             "Published": "2026-05-06",
             "Source URL": "https://news.example.org/a2", "Fact grade": "Probable"},
            {"Article ID": "A3", "Headline": core.NOT_FOUND, "Published": "2026-05-07",
             "Source URL": "https://news.example.org/a3"},
        ],
        [{"Article ID": "A2", "Fact": "The firm opened a new office.", "Fact grade": "Probable"}],
    )  # fmt: skip
    rows = build_signals(profile, {"news": news}).main.rows
    assert [r["Signal"] for r in rows] == ["Firm to acquire a rival."]


def test_no_source_is_dropped_and_logged(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    news = news_table(
        [{"Article ID": "A1", "Published": "2026-05-05", "Source URL": core.NOT_FOUND}],
        [{"Article ID": "A1", "Fact": "The company announced a spin-off of a unit."}],
    )
    table = build_signals(profile, {"news": news})
    assert table.main.rows == []
    assert table.stats["dropped"] == 1
    [ex] = table.exclusions
    assert ex.reason == "no source" and "spin-off" in ex.key


def test_duplicates_merge_and_keep_best_grade_and_all_evidence(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    articles = [
        {"Article ID": "A1", "Published": "2026-05-05", "Source URL": "https://one.example.org/x"},
        {"Article ID": "A2", "Published": "2026-05-09", "Source URL": "https://two.example.org/y"},
    ]
    facts = [
        {
            "Article ID": "A1",
            "Fact": "The firm completes the merger of its two divisions.",
            "Fact grade": "Lead",
        },
        {
            "Article ID": "A2",
            "Fact": "the firm completes   the MERGER of its two divisions.",
            "Fact grade": "Confirmed",
        },
    ]
    sources = sources_table({"S1": "https://one.example.org/x", "S2": "https://two.example.org/y"})
    table = build_signals(profile, {"news": news_table(articles, facts), "sources": sources})
    [sig] = table.main.rows
    assert sig["Fact grade"] == "Confirmed"
    assert sig["Evidence (source codes)"] == "S1, S2"
    assert sig["Event date"] == "2026-05-09"


def test_sort_event_date_desc_unknown_last_then_type(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    articles = [
        {"Article ID": f"A{i}", "Published": d, "Source URL": f"https://n.example.org/{i}"}
        for i, d in enumerate(["2026-01-01", "2026-09-01", core.UNKNOWN, "2026-09-01"])
    ]
    facts = [
        {"Article ID": "A0", "Fact": "The firm announces a dividend for all shareholders."},
        {"Article ID": "A1", "Fact": "The firm announces an acquisition of a smaller rival."},
        {"Article ID": "A2", "Fact": "The firm announces a recall of one product line."},
        {"Article ID": "A3", "Fact": "The firm announces a dividend increase for the year."},
    ]
    rows = build_signals(profile, {"news": news_table(articles, facts)}).main.rows
    assert [(r["Event date"], r["Type"]) for r in rows] == [
        ("2026-09-01", "M&A / divestiture"),
        ("2026-09-01", "Funding / financial"),
        ("2026-01-01", "Funding / financial"),
        (core.UNKNOWN, "Regulatory event"),
    ]


# ---- leadership ---------------------------------------------------------------------------------
def test_leadership_start_departure_and_move(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    url = "https://pr.example.org/lead"
    rows = [
        {"Name": "Ada Example", "Role": "Chief Data Officer", "Start date": "2026-04-01",
         "End or retire date": core.NOT_APPLICABLE, "Status": "Current", "Source URL": url},
        {"Name": "Bo Sample", "Role": "Head of Finance", "Start date": "2019-01-01",
         "End or retire date": "2026-06-30", "Status": "Departed", "Source URL": url},
        {"Name": "Cy Placeholder", "Role": "Head of Sales", "Start date": "2018-01-01",
         "End or retire date": core.UNKNOWN, "Status": "Moved to divested business",
         "Source URL": url},
        {"Name": "Di Old", "Role": "Chief Counsel", "Start date": "2015-01-01",
         "End or retire date": core.NOT_APPLICABLE, "Status": "Current", "Source URL": url},
        {"Name": "Ed Unsure", "Role": "Chief Risk Officer", "Start date": "2026-05-01",
         "End or retire date": core.NOT_APPLICABLE, "Status": "Unverified", "Source URL": url},
    ]  # fmt: skip
    table = build_signals(profile, {"people": leadership_table(rows)})
    got = {r["Signal"]: r for r in by_type(table, "Leadership change")}
    assert len(got) == 4  # the long-standing current executive is not a signal
    assert (
        got["Ada Example started as Chief Data Officer on 2026-04-01."]["Fact grade"] == "Confirmed"
    )
    assert got["Ed Unsure started as Chief Risk Officer on 2026-05-01."]["Fact grade"] == "Lead"
    assert "Bo Sample is recorded as leaving the role of Head of Finance on 2026-06-30." in got
    assert got["Cy Placeholder, Head of Sales, moved to a divested business."]["Event date"] == (
        "2018-01-01"
    )


def test_leadership_without_source_is_dropped(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    rows = [{"Name": "Ada Example", "Role": "Chief Data Officer", "Start date": "2026-04-01",
             "Status": "Current", "Source URL": core.NOT_FOUND}]  # fmt: skip
    table = build_signals(profile, {"people": leadership_table(rows)})
    assert table.main.rows == [] and table.stats["dropped"] == 1


# ---- stored signals -----------------------------------------------------------------------------
def test_seeds_map_types_and_need_a_source(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    seeds = [
        SignalSeed("New CIO named", "leadership_change", "Alpha", "confirmed",
                   date(2026, 7, 1), date(2026, 6, 28), ("S2",)),
        SignalSeed("Platform team hiring build roles", "build_role_posting", "", "Probable",
                   None, None, (), ("https://careers.example.org/1",)),
        SignalSeed("Firm sells a division", "funding_ma", "", "hypothesis", None, None, ("S4",)),
        SignalSeed("Firm prices notes offering", "funding_ma", "", "probable", None, None, ("S5",)),
        SignalSeed("Site visit on Tuesday", "news_event", "", "lead", None, None, ("S6",)),
        SignalSeed("Tech detected", "tech_detection", "", "lead", None, None, ("S6",)),
        SignalSeed("Sourceless leadership", "leadership_change"),
    ]  # fmt: skip
    table = build_signals(profile, {}, seeds)
    by = {r["Signal"]: r for r in table.main.rows}
    assert by["New CIO named."]["Type"] == "Leadership change"
    assert by["New CIO named."]["Lane"] == "Alpha"
    assert by["New CIO named."]["Detected"] == "2026-07-01"
    assert by["New CIO named."]["Event date"] == "2026-06-28"
    assert by["Platform team hiring build roles."]["Type"] == "Build-role hiring"
    assert by["Platform team hiring build roles."]["Detected"] == profile.run_date.isoformat()
    assert by["Firm prices notes offering."]["Type"] == "Funding / financial"
    assert "Firm sells a division." not in by  # funding_ma with no wording to decide: dropped
    assert "Site visit on Tuesday." not in by
    assert "Tech detected." not in by
    assert "Sourceless leadership." not in by
    reasons = sorted(e.reason for e in table.exclusions)
    assert reasons == [
        "no source",
        "unknown signal type",
        "unknown signal type",
        "unknown signal type",
    ]
    assert table.stats["dropped"] == 4


def test_every_emitted_type_and_grade_is_valid(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    tables = {
        "jobs": jobs_table(spike_rows(profile, acc["lanes"][0], 6, 2)),
        "news": news_table(
            [
                {
                    "Article ID": "A1",
                    "Published": "2026-05-05",
                    "Source URL": "https://n.example.org/1",
                }
            ],
            [{"Article ID": "A1", "Fact": "Leader steps down as chief executive."}],
        ),
    }
    table = build_signals(profile, tables, [SignalSeed("Merger talks", "x", source_codes=("S1",))])
    assert table.main.columns == core.SIGNAL_COLUMNS
    for row in table.main.rows:
        assert set(row) == set(core.SIGNAL_COLUMNS)
        assert row["Type"] in core.SIGNAL_TYPES
        assert row["Fact grade"] in core.FACT_GRADES
        assert row["Evidence (source codes)"].strip()
        assert all(str(v).strip() for v in row.values())
    assert sum(table.stats["per_type"].values()) == len(table.main.rows) == table.stats["total"]


# ---- robustness ---------------------------------------------------------------------------------
def test_missing_tables_are_skipped_and_noted(acc: dict[str, Any]) -> None:
    table = build_signals(make_profile(acc), {})
    assert table.key == "signals" and table.title == "Signals"
    assert table.main.rows == []
    assert table.stats["skipped_families"] == ["jobs", "news", "people"]
    assert any("jobs" in n for n in table.notes)


def test_table_without_the_expected_sheet_is_skipped(acc: dict[str, Any]) -> None:
    odd = RepairedTable("jobs", "Jobs", [Sheet("Something else", ("A",), [{"A": "1"}])])
    table = build_signals(make_profile(acc), {"jobs": odd, "news": news_table([])})
    assert table.stats["skipped_families"] == ["jobs", "people"]


def test_rows_with_missing_columns_do_not_crash(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    jobs = RepairedTable("jobs", "Jobs", [Sheet("Job postings", ("Title",), [{"Title": "x"}])])
    news = RepairedTable(
        "news", "News", [Sheet("News", ("Headline",), [{"Headline": "Firm acquires"}])]
    )
    table = build_signals(profile, {"jobs": jobs, "news": news})
    assert table.main.rows == []  # the headline has no source


def test_idempotent_and_inputs_untouched(acc: dict[str, Any]) -> None:
    profile = make_profile(acc)
    tables = {
        "jobs": jobs_table(spike_rows(profile, acc["lanes"][0], 6, 2)),
        "news": news_table(
            [
                {
                    "Article ID": "A1",
                    "Published": "2026-05-05",
                    "Source URL": "https://n.example.org/1",
                }
            ],
            [{"Article ID": "A1", "Fact": "Firm completes the merger."}],
        ),
    }
    before = copy.deepcopy(tables)
    first = build_signals(profile, tables)
    second = build_signals(profile, tables)
    assert first.main.rows == second.main.rows
    assert first.stats == second.stats
    assert tables["jobs"].main.rows == before["jobs"].main.rows
    assert tables["news"].sheets[1].rows == before["news"].sheets[1].rows


def test_two_accounts_share_no_state() -> None:
    a, b = make_profile(ACCOUNT_A), make_profile(ACCOUNT_B)
    ta = build_signals(a, {"jobs": jobs_table(spike_rows(a, "Alpha CRM", 6, 2))})
    tb = build_signals(b, {"jobs": jobs_table(spike_rows(b, "Gamma BI", 6, 2))})
    assert "Quillfern" in ta.main.rows[0]["Signal"] and "Marlow" not in ta.main.rows[0]["Signal"]
    assert "Marlow" in tb.main.rows[0]["Signal"] and "Quillfern" not in tb.main.rows[0]["Signal"]


# ---- helpers ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("CEO retires after ten years", "Leadership change"),
        ("Chief financial officer resigns", "Leadership change"),
        ("Company names new CRO", "Leadership change"),
        ("Announces separation into two companies", "M&A / divestiture"),
        ("Spin-off of the devices unit completed", "M&A / divestiture"),
        ("Voluntary recall of lot 12", "Regulatory event"),
        ("Consent decree entered", "Regulatory event"),
        ("Declares quarterly dividend", "Funding / financial"),
        ("Share repurchase of 2 billion", "Funding / financial"),
        ("Fiscal results date set for November", "Funding / financial"),
        ("Cloud migration program kicks off", "Program or migration"),
        ("Go-live of the new platform", "Program or migration"),
        ("A new coffee machine arrives", None),
        ("Microsoft factor precious", None),  # role words must be whole words
        ("", None),
    ],
)
def test_classify_text(text: str, kind: str | None) -> None:
    assert classify_text(text) == kind


@pytest.mark.parametrize(
    ("word", "title", "kind"),
    [
        ("leadership_change", "", "Leadership change"),
        ("build_role_posting", "", "Build-role hiring"),
        ("Platform hiring spike", "", "Platform hiring spike"),
        ("m&a", "", "M&A / divestiture"),
        ("funding_ma", "Firm to acquire rival", "M&A / divestiture"),
        ("funding_ma", "Firm pays a dividend", "Funding / financial"),
        ("funding_ma", "Firm hosts a picnic", None),
        ("regulatory", "", "Regulatory event"),
        ("news_event", "FDA warning letter received", "Regulatory event"),
        ("other", "nothing relevant", None),
        ("tech_detection", "", None),
    ],
)
def test_map_signal_type(word: str, title: str, kind: str | None) -> None:
    assert map_signal_type(word, title) == kind


def test_small_helpers() -> None:
    assert parse_day("2026-02-03T10:00:00Z") == date(2026, 2, 3)
    assert parse_day("2026-13-40") is None
    assert parse_day(core.UNKNOWN) is None
    assert normalise_grade("Hypothesis") == "Lead"
    assert normalise_grade(" CONFIRMED ") == "Confirmed"
    assert normalise_grade("probable") == "Probable"
