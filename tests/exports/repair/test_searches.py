"""The Web searches table: one row per normalised query."""

from __future__ import annotations

from datetime import date, datetime

from exports.repair import core
from exports.repair.core import WorkspaceProfile
from exports.repair.records import SearchCall
from exports.repair.searches import build_searches


def profile(name: str = "Zephyrcore Systems") -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name=name,
        account_short=name.split()[0],
        domain="zephyrcore.example",
        run_date=date(2026, 10, 30),
        window_from=date(2025, 10, 30),
        window_to=date(2026, 10, 30),
    )


def rows(table):
    return table.main.rows


def test_one_row_per_normalised_query() -> None:
    calls = [
        SearchCall("Zephyrcore  CIO appointed", provider="alpha", pages=2),
        SearchCall("zephyrcore cio appointed!", provider="beta", pages=3),
        SearchCall("ZEPHYRCORE: CIO, Appointed", provider="alpha", pages=1),
        SearchCall("zephyrcore migration to cloud", provider="alpha", pages=4),
    ]
    table = build_searches(profile(), calls)
    assert table.key == "searches" and table.title == "Web searches"
    assert [s.name for s in table.sheets] == ["Web searches"]
    assert table.main.columns == core.SEARCH_COLUMNS
    assert len(rows(table)) == 2
    first = next(r for r in rows(table) if r["Query"].startswith("Zephyrcore"))
    assert first["Query"] == "Zephyrcore CIO appointed"  # the first-seen wording
    assert first["Runs"] == "3"
    assert first["Pages returned (total)"] == "6"
    assert first["Providers"] == "6th agents"
    assert table.stats == {
        "calls_in": 4,
        "unique_queries": 2,
        "repeats_removed": 2,
        "dropped_empty": 0,
    }


def test_providers_sites_sorted_unique() -> None:
    calls = [
        SearchCall("q one", provider="beta", sites=("Z.example", "a.example")),
        SearchCall("q one", provider="alpha", sites=("a.example", "A.example", " ")),
        SearchCall("q one", provider="alpha", sites=("m.example",)),
    ]
    r = rows(build_searches(profile(), calls))[0]
    assert r["Providers"] == "6th agents"
    assert r["Sites"] == "a.example, m.example, Z.example"


def test_missing_values_are_marked_not_blank() -> None:
    r = rows(build_searches(profile(), [SearchCall("lonely query")]))[0]
    assert r["Providers"] == core.NOT_FOUND
    assert r["Sites"] == core.NOT_FOUND
    assert r["First run"] == core.UNKNOWN
    assert r["Purpose (Coverage item #)"] == core.UNKNOWN
    assert r["Used (Y/N)"] == core.UNKNOWN
    assert r["Runs"] == "1"
    assert r["Pages returned (total)"] == "0"
    assert all(v.strip() for v in r.values())


def test_first_run_is_the_earliest() -> None:
    calls = [
        SearchCall("q", run_at=datetime(2026, 10, 20, 9)),
        SearchCall("q", run_at=datetime(2026, 10, 2, 23)),
        SearchCall("q"),
    ]
    assert rows(build_searches(profile(), calls))[0]["First run"] == "2026-10-02"


def test_used_flag() -> None:
    def used(*flags):
        calls = [SearchCall("q", used=f) for f in flags]
        return rows(build_searches(profile(), calls))[0]["Used (Y/N)"]

    assert used(False, True, None) == "Y"
    assert used(True) == "Y"
    assert used(False, False) == "N"
    assert used(None, None) == core.UNKNOWN
    assert used(False, None) == core.UNKNOWN  # one run unknown: not claimed unused


def test_purpose_is_the_first_non_empty() -> None:
    calls = [
        SearchCall("q", purpose=""),
        SearchCall("q", purpose="  "),
        SearchCall("q", purpose="7"),
        SearchCall("q", purpose="9"),
    ]
    assert rows(build_searches(profile(), calls))[0]["Purpose (Coverage item #)"] == "7"


def test_empty_queries_are_dropped_and_logged() -> None:
    calls = [SearchCall(""), SearchCall("   ", provider="alpha"), SearchCall("?!", provider="beta"),
             SearchCall("real query", provider="alpha")]  # fmt: skip
    table = build_searches(profile(), calls)
    assert len(rows(table)) == 1
    assert [e.reason for e in table.exclusions] == ["empty query"] * 3
    assert {e.table for e in table.exclusions} == {"searches"}
    assert table.stats["calls_in"] == 4
    assert table.stats["repeats_removed"] == 0
    assert table.stats["dropped_empty"] == 3


def test_negative_pages_do_not_reduce_the_total() -> None:
    calls = [SearchCall("q", pages=5), SearchCall("q", pages=-3)]
    assert rows(build_searches(profile(), calls))[0]["Pages returned (total)"] == "5"


def test_no_calls() -> None:
    table = build_searches(profile(), [])
    assert rows(table) == []
    assert table.stats["unique_queries"] == 0


def test_rows_are_ordered_by_first_run_unknown_last() -> None:
    calls = [
        SearchCall("zeta", run_at=datetime(2026, 10, 3)),
        SearchCall("undated"),
        SearchCall("alpha", run_at=datetime(2026, 10, 3)),
        SearchCall("early", run_at=datetime(2026, 9, 1)),
    ]
    queries = [r["Query"] for r in rows(build_searches(profile(), calls))]
    assert queries == ["early", "alpha", "zeta", "undated"]


def test_accents_and_case_merge() -> None:
    calls = [SearchCall("Café opening"), SearchCall("cafe OPENING")]
    assert len(rows(build_searches(profile(), calls))) == 1


def test_two_accounts_get_the_same_treatment() -> None:
    calls = [SearchCall("alpha  query", provider="p1", pages=1),
             SearchCall("Alpha query", provider="p2", pages=2)]  # fmt: skip
    a = build_searches(profile("Zephyrcore Systems"), calls)
    b = build_searches(profile("Quillmark Holdings"), calls)
    assert rows(a) == rows(b)
    assert a.stats == b.stats


def test_idempotent() -> None:
    calls = [SearchCall("q a", provider="x", pages=1), SearchCall("Q  A", provider="y", pages=2)]
    first = build_searches(profile(), calls)
    second = build_searches(profile(), calls)
    assert rows(first) == rows(second)
    # feeding the built table back as calls gives the same query set
    again = build_searches(profile(), [SearchCall(r["Query"]) for r in rows(first)])
    assert [r["Query"] for r in rows(again)] == [r["Query"] for r in rows(first)]


def test_every_cell_is_filled() -> None:
    table = build_searches(profile(), [SearchCall("q", provider="x")])
    for r in rows(table):
        assert set(r) == set(core.SEARCH_COLUMNS)
        assert all(v.strip() for v in r.values())
