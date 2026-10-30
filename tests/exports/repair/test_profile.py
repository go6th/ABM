"""The workspace profile is built from each workspace's own data and names no account."""

from __future__ import annotations

from datetime import date

import pytest

from exports.repair.profile import profile_from_parts, short_name, window_start


@pytest.mark.parametrize(
    ("run", "months", "start"),
    [
        (date(2026, 10, 19), 12, date(2025, 10, 19)),
        (date(2026, 2, 28), 12, date(2025, 2, 28)),
        (date(2024, 2, 29), 12, date(2023, 2, 28)),  # no 29 February the year before
        (date(2026, 1, 31), 1, date(2025, 12, 31)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
    ],
)
def test_window_start_keeps_the_day_or_the_month_end(run: date, months: int, start: date) -> None:
    assert window_start(run, months) == start


@pytest.mark.parametrize(
    ("name", "domain", "short"),
    [
        ("Acme Holdings, Inc.", "acme.com", "Acme"),
        ("Zeta Group plc", "zeta.co.uk", "Zeta"),
        ("", "https://www.northwind.example", "Northwind"),
        ("Contoso", "contoso.com", "Contoso"),
    ],
)
def test_short_name_drops_the_legal_form(name: str, domain: str, short: str) -> None:
    assert short_name(name, domain) == short


def test_two_workspaces_get_their_own_profile() -> None:
    a = profile_from_parts(
        name="Acme Holdings, Inc.",
        domain="https://www.acme.com",
        run_date=date(2026, 10, 19),
        lock={
            "allowed_domains": ["acme.com", "acme.co.uk"],
            "subsidiaries": [{"name": "Acme Labs", "domain": "acmelabs.io"}],
            "careers_hosts": ["acme.wd1.myworkdayjobs.com"],
            "brand": "acme",
        },
        watch_config={
            "entities": [
                {
                    "label": "Zeta",
                    "patterns": ["applying for a position with Zeta"],
                    "counts_as_account": False,
                    "before": "2026-04-01",
                }
            ]
        },
        people_consent=True,
    )
    b = profile_from_parts(
        name="Northwind Traders", domain="northwind.example", run_date=date(2026, 5, 1)
    )
    assert a.domains == {"acme.com", "acme.co.uk", "acmelabs.io"}
    assert "myworkdayjobs.com" not in a.domains  # a job board never becomes the account's domain
    assert "acme.wd1.myworkdayjobs.com" in a.careers_hosts
    assert [(e.label, e.counts_as_account) for e in a.entities] == [
        ("Acme Labs", True),
        ("Zeta", False),
    ]
    assert a.entities[1].before == "2026-04-01"
    assert a.people_consent and not b.people_consent
    assert b.domains == {"northwind.example"} and b.entities == ()
    assert b.window_from == date(2025, 5, 1) and a.window_from == date(2025, 10, 19)
    assert all("acme" not in x.lower() for x in b.aliases)


def test_malformed_owner_entities_are_ignored() -> None:
    p = profile_from_parts(
        name="Acme",
        domain="acme.com",
        run_date=date(2026, 1, 1),
        watch_config={"entities": ["x", {"patterns": ["y"]}, {"label": " "}, {"label": "Ok"}]},
    )
    assert [e.label for e in p.entities] == ["Ok"]


def test_short_name_drops_a_dangling_conjunction_and_can_be_set_by_the_owner() -> None:
    assert short_name("Becton, Dickinson and Company", "x.com") == "Becton, Dickinson"
    assert short_name("Johnson & Johnson") == "Johnson & Johnson"
    p = profile_from_parts(
        name="Becton, Dickinson and Company",
        domain="x.com",
        run_date=date(2026, 1, 1),
        watch_config={"short_name": "BD"},
    )
    assert p.account_short == "BD" and p.label == "BD"
    assert "Becton, Dickinson" in p.aliases and "BD" in p.aliases
