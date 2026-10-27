"""A person row needs a real human name (master prompt v2 section 5): titles, fragments and
quotes a source called a person are excluded, never listed."""

from __future__ import annotations

import pytest

from people.names import looks_like_person


@pytest.mark.parametrize(
    "name",
    ["Rand Ballard", "Simrit Sandhu", "Arun Ramasubramanian", "J. R. Smith", "Mary-Ann O'Neil"],
)
def test_human_names_pass(name: str) -> None:
    assert looks_like_person(name)


@pytest.mark.parametrize(
    "name",
    [
        "Senior Vice President, Marketing",
        "Chief Customer Officer",
        "Director of IT",
        '"We are investing in service"',
        "Vizient",
        "team lead 2",
        "",
        None,
        "President and CEO",
    ],
)
def test_titles_fragments_and_quotes_fail(name: str | None) -> None:
    assert not looks_like_person(name)
