"""The account's own identity never becomes a restricted term (staging 2026-11-06, Olympus): the
account was added by its domain, so its name and brand were "olympusamerica"; a people record
named "Olympus Corporation" read as a person, was guarded as a name off the People List, and every
text naming the company was withheld from the client files (280 texts, all 22 job descriptions)."""

from __future__ import annotations

from exports.policy import restricted_terms
from exports.readers.common import own_names
from people.names import looks_like_person
from tests.exports.corpus import account


def test_a_company_name_is_not_a_person() -> None:
    assert not looks_like_person("Olympus Corporation")
    assert not looks_like_person("Acme Holdings")
    assert not looks_like_person("Contoso Ltd")
    assert looks_like_person("Bob White") and looks_like_person("Maria de la Cruz")


def test_the_home_page_title_names_the_account_itself() -> None:
    lock = {
        "legal_name": "olympusamerica",
        "brand": "olympusamerica",
        "home_title": "Olympus Corporation of the Americas | Olympus",
    }
    content = account().model_copy(update={"entity_lock": lock})
    own = own_names(content)
    assert "Olympus Corporation of the Americas" in own and "Olympus" in own
    kept = restricted_terms(["Olympus Corporation", "Zorblax Industries"], "6th", own)
    assert kept == ("Zorblax Industries",)


def test_internal_only_people_stay_out_of_the_workspace_zip() -> None:
    """ADR-0025 narrowed (owner decision 2026-11-06): an internal-only person is on the People
    List and in the web tables, never in the zip's people members."""
    import json

    from exports.config import load_export_config
    from exports.workspace_files import derived_people_member, people_grid_member

    base = account()
    hidden = base.people[0].model_copy(update={"internal": True})
    content = base.model_copy(update={"people": (hidden, *base.people[1:])})
    rules = load_export_config().rules.workspace
    grid = people_grid_member(content, rules, str, None).decode()
    derived = json.loads(derived_people_member(content, rules, str, None))
    assert hidden.name not in grid and base.people[1].name in grid
    assert hidden.name not in {p["name"] for p in derived["people"]}


def test_a_person_named_on_a_public_page_may_be_named() -> None:
    """Owner decision 2026-11-06: an executive named in a release the research read (a mention)
    is printed even when the people search did not list them; a tenant exclusion or an erasure
    still guards the name, and a person known only from a search record stays guarded."""
    from types import SimpleNamespace

    from exports.readers.pg import may_be_named

    def person(key: str, origin: str, url: str | None, reason: str | None = None) -> object:
        return SimpleNamespace(
            person_key=key,
            origin=origin,
            mention_url=url,
            classification=SimpleNamespace(reason=reason),
        )

    ceo = person("ceo", "mention", "https://example.com/release")
    assert may_be_named(ceo, set(), set())
    assert not may_be_named(
        person("x", "mention", "https://e.com/r", "tenant_exclusion"), set(), set()
    )
    assert not may_be_named(ceo, set(), {"ceo"})
    assert not may_be_named(person("s", "title_search", None), set(), set())
    assert may_be_named(person("s", "title_search", None), {"s"}, set())
