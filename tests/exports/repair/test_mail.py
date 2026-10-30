"""Mail and drive table: not connected, nothing matched, matched items, privacy marker."""

from __future__ import annotations

from datetime import date

import pytest

from exports.repair import core
from exports.repair.core import WorkspaceProfile
from exports.repair.mail import (
    INTERNAL_MARKER,
    build_mail,
    mentions_account,
    normalise_kind,
)
from exports.repair.records import MailItem

ACCOUNTS = {
    "a": ("Quillfern Holdings", "Quillfern", "quillfern.example", ("QFH",)),
    "b": ("Marlow Tideworks", "Marlow", "marlow.test", ("Tideworks",)),
}


def make_profile(key: str = "a", *, connected: bool = True) -> WorkspaceProfile:
    name, short, domain, aliases = ACCOUNTS[key]
    return WorkspaceProfile(
        account_name=name,
        account_short=short,
        domain=domain,
        run_date=date(2026, 10, 31),
        window_from=date(2025, 11, 1),
        window_to=date(2026, 10, 31),
        aliases=aliases,
        mail_drive_connected=connected,
    )


@pytest.fixture(params=["a", "b"])
def key(request: pytest.FixtureRequest) -> str:
    return request.param


def test_not_connected_is_one_placeholder_row(key: str) -> None:
    items = [MailItem("Should be ignored", "thread")]
    table = build_mail(make_profile(key, connected=False), items)
    assert (table.key, table.title, table.internal) == ("mail", "From your mail and drive", True)
    [row] = table.main.rows
    assert row["Title"] == core.not_collected("mail and drive not connected")
    assert all(row[c] == core.NOT_APPLICABLE for c in core.MAIL_COLUMNS if c != "Title")
    assert table.stats["connected"] is False
    assert any("Coverage items 1 and 2" in n and "Not done" in n for n in table.notes)
    assert INTERNAL_MARKER in table.notes and table.main.private


def test_connected_without_matches(key: str) -> None:
    table = build_mail(make_profile(key), [])
    [row] = table.main.rows
    assert row["Title"] == core.not_collected("no matching mail or files found")
    assert row["Kind"] == core.NOT_APPLICABLE
    assert table.stats["connected"] is True and table.stats["items"] == 0
    assert INTERNAL_MARKER in table.notes and table.main.private


def test_items_are_filled_deduped_and_sorted(key: str) -> None:
    profile = make_profile(key)
    acct = profile.account_short
    items = [
        MailItem("Old plan", "File", "ann@x.test", date(2026, 1, 5), "Plan for the year",
                 "Planning", "https://drive.example/1"),
        MailItem(f"{acct} renewal thread", "thread", "bob@x.test", date(2026, 9, 9), "", "", ""),
        MailItem(f"{acct} renewal thread", "thread", "BOB@x.test", date(2026, 9, 9), "Late summary",
                 "", "https://mail.example/t"),
        MailItem("Undated note", "weird", "", None, "Mentions nothing", "", ""),
        MailItem("Newest", "email", "cy@x.test", date(2026, 10, 20), f"About {acct}.", "", ""),
    ]  # fmt: skip
    table = build_mail(profile, items)
    assert [r["Title"] for r in table.main.rows] == [
        "Newest",
        f"{acct} renewal thread",
        "Old plan",
        "Undated note",
    ]
    rows = {r["Title"]: r for r in table.main.rows}
    renewal = rows[f"{acct} renewal thread"]
    assert renewal["Summary"] == "Late summary" and renewal["Link"] == "https://mail.example/t"
    assert renewal["Kind"] == "Thread" and renewal["Date"] == "2026-09-09"
    assert renewal["Relevance"] == "Mentions the account"
    assert rows["Newest"]["Relevance"] == "Mentions the account"
    assert rows["Old plan"]["Relevance"] == "Planning" and rows["Old plan"]["Kind"] == "File"
    undated = rows["Undated note"]
    assert undated["Date"] == core.UNKNOWN and undated["From"] == core.NOT_FOUND
    assert undated["Kind"] == core.NOT_FOUND and undated["Link"] == core.NOT_FOUND
    assert undated["Relevance"] == "Matched by search"
    assert table.stats["items"] == 4
    assert table.exclusions[0].reason == "duplicate"
    for row in table.main.rows:
        assert set(row) == set(core.MAIL_COLUMNS)
        assert all(str(v).strip() for v in row.values())
    assert table.main.private and INTERNAL_MARKER in table.notes


def test_same_title_different_sender_or_date_is_kept(key: str) -> None:
    items = [
        MailItem("Budget", "thread", "a@x.test", date(2026, 3, 1)),
        MailItem("Budget", "thread", "b@x.test", date(2026, 3, 1)),
        MailItem("Budget", "thread", "a@x.test", date(2026, 3, 2)),
    ]
    assert len(build_mail(make_profile(key), items).main.rows) == 3


def test_relevance_matches_alias_but_not_inside_words() -> None:
    profile = make_profile("a")
    assert mentions_account(profile, "Call with QFH about renewal")
    assert mentions_account(profile, "quillfern holdings update")
    assert not mentions_account(profile, "AQFHB is not an alias hit")
    assert not mentions_account(
        profile, "qfh lowercase short alias"
    )  # short aliases are exact-case
    assert mentions_account(make_profile("b"), "The tideworks contract")
    assert not mentions_account(make_profile("b"), "The Quillfern contract")


@pytest.mark.parametrize(
    ("word", "kind"),
    [("Thread", "Thread"), ("email", "Thread"), ("Document", "File"), ("drive", "File"),
     ("", core.NOT_FOUND), ("calendar", core.NOT_FOUND)],
)  # fmt: skip
def test_normalise_kind(word: str, kind: str) -> None:
    assert normalise_kind(word) == kind


def test_idempotent(key: str) -> None:
    profile = make_profile(key)
    items = [MailItem("T", "file", "a@x.test", date(2026, 3, 1), "S")]
    assert build_mail(profile, items).main.rows == build_mail(profile, items).main.rows
