"""Dates read from URLs and text: strict, never from the clock."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from exports.repair.dates import date_from_text, date_from_url, is_collection_date, real_date

D = date(2026, 10, 12)


@pytest.mark.parametrize(
    "url",
    [
        "https://news.example/2026/10/12/quarterly-update",
        "https://news.example/2026/10/12",
        "https://news.example/2026-10-12-quarterly-update",
        "https://news.example/blog/2026_10_12_update",
        "https://news.example/press/20261012",
        "https://news.example/press/20261012-results.html",
        "https://news.example/2026/1/2/x",
        "https://news.example/a?d=2026-10-12",
    ],
)
def test_url_dates_are_read(url: str) -> None:
    got = date_from_url(url)
    assert got is not None
    assert got.year == 2026 and got.month in (1, 10)


def test_url_date_value() -> None:
    assert date_from_url("https://n.example/2026/10/12/a/") == D
    assert date_from_url("https://n.example/2026-10-12-a") == D
    assert date_from_url("https://n.example/20261012") == D
    assert date_from_url("https://n.example/2026/1/2/x") == date(2026, 1, 2)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://n.example/",
        "https://n.example/26/10/62156538/",  # an id, not a date
        "https://n.example/2026/10/",  # no day
        "https://n.example/2026/13/12/x",  # month 13
        "https://n.example/2026/02/30/x",  # no 30 February
        "https://n.example/1899/10/12/x",  # year out of range
        "https://n.example/2101/10/12/x",
        "https://n.example/id/202610121",  # nine digits
        "https://n.example/id/62156538",  # eight digits, year 6215
        "https://n.example/2026/10/62156538",  # day is an id
        "https://n.example/2026-10_12",  # mixed separators
        "not a url at all",
    ],
)
def test_url_non_dates_are_rejected(url: str) -> None:
    assert date_from_url(url) is None


def test_url_host_digits_are_not_a_date() -> None:
    assert date_from_url("https://20261012.example/page") is None


@pytest.mark.parametrize(
    "text",
    [
        "Published 2026-10-12.",
        "12 October 2026",
        "12th October 2026",
        "12 Oct 2026",
        "12 Oct. 2026",
        "October 12, 2026",
        "October 12 2026",
        "Oct 12, 2026",
        "Oct. 12th, 2026",
        "OCTOBER 12, 2026",
        "Updated on 12 october 2026 at noon",
    ],
)
def test_text_dates_are_read(text: str) -> None:
    assert date_from_text(text) == D


def test_text_first_plausible_match_wins() -> None:
    assert date_from_text("From 3 March 2025 to 4 April 2026") == date(2025, 3, 3)
    # an impossible first match is skipped
    assert date_from_text("31 February 2026, then 1 March 2026") == date(2026, 3, 1)
    assert date_from_text("2026-10-12 and October 1, 2020") == D


def test_text_sept_and_may() -> None:
    assert date_from_text("Sept 5, 2026") == date(2026, 9, 5)
    assert date_from_text("5 May 2026") == date(2026, 5, 5)


@pytest.mark.parametrize(
    "text",
    ["", "no date here", "October 2026", "Q3 2026", "12 October", "2026-13-01", "12 Octobre 2026",
     "1890-01-01", "Marching 12, 2026"],
)  # fmt: skip
def test_text_non_dates(text: str) -> None:
    assert date_from_text(text) is None


def test_real_date_picks_first_usable() -> None:
    assert real_date(None, "", "no date", D) == D
    assert real_date("12 October 2026", date(2020, 1, 1)) == D
    assert real_date(datetime(2026, 10, 12, 23, 59)) == D
    assert real_date(None, "nope") is None
    assert real_date() is None


def test_real_date_skips_implausible_dates_and_other_types() -> None:
    assert real_date(date(1500, 1, 1), 42, D) == D
    assert real_date(date(3000, 1, 1)) is None


def test_collection_date_detection() -> None:
    assert is_collection_date(D, D)
    assert is_collection_date(D, datetime(2026, 10, 12, 8, 30))
    assert is_collection_date(datetime(2026, 10, 12, 1), date(2026, 10, 12))
    assert not is_collection_date(D, date(2026, 10, 13))
    assert not is_collection_date(None, D)
    assert not is_collection_date(D, None)
    assert not is_collection_date(None, None)


def test_functions_are_pure() -> None:
    url = "https://n.example/2026/10/12/a"
    assert date_from_url(url) == date_from_url(url)
    assert date_from_text("12 October 2026") == date_from_text("12 October 2026")
