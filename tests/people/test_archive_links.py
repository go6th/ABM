"""People without a profile link are matched to the profile pages the research already holds
(``people.archive_links``, ``people.mentions.resolve_unlinked``). SYNTHETIC data only."""

from __future__ import annotations

from typing import Any

import pytest

from people.archive_links import ProfileLink, candidates_for, parse_profile_title, unlinked
from people.config import load_people_config
from people.mentions import resolve_unlinked
from people.models import PersonPage, RawPerson
from people.ports import SourceUnavailable
from people.urls import canonical_profile_url
from tests.fakes.people import Corpus
from tests.people.kit import Kit


def _link(url: str, title: str | None) -> ProfileLink:
    return ProfileLink(url, title, "t/archive/x", "2026-10-27T10:00:00+00:00")


def test_profile_titles_split_into_name_headline_and_company() -> None:
    t = parse_profile_title("Jane Doe - Chief Information Officer - Synthetic Acme | LinkedIn")
    assert t is not None
    assert (t.name, t.headline, t.company) == (
        "Jane Doe",
        "Chief Information Officer",
        "Synthetic Acme",
    )
    t = parse_profile_title("Raj Patel – Senior Manager, CRM – LinkedIn")
    assert t is not None and (t.name, t.headline, t.company) == (
        "Raj Patel",
        "Senior Manager, CRM",
        None,
    )
    assert parse_profile_title("Jane Doe | LinkedIn").name == "Jane Doe"  # type: ignore[union-attr]
    # an empty title and a one-word title name nobody (a company page is kept off the archive's
    # profile kind by its address, ``archive.link_kind``, not by its title)
    assert parse_profile_title("") is None and parse_profile_title(None) is None
    assert parse_profile_title("Careers | LinkedIn") is None


def test_candidates_need_the_title_to_name_the_account_a_name_alone_attaches_nobody() -> None:
    """Security review 2026-10-29, F7 (pipeline 16): the page's company part must name the
    account's brand. A same-name person at another company, or a title with no company, is
    dropped, never attached."""
    links = [
        _link("https://www.linkedin.com/in/jane-doe", "Jane Doe - CIO - Synthetic Acme | LinkedIn"),
        _link("https://www.linkedin.com/in/jane-doe-2", "Jane Doe - CFO - Other Co | LinkedIn"),
        _link("https://www.linkedin.com/in/jane-doe-3", "Jane Doe - Manager | LinkedIn"),
        _link("https://www.linkedin.com/in/no-title", None),
    ]
    found = candidates_for(
        "jane doe", links, account_domain="synthetic-acme.example.com", brand="synthetic"
    )
    assert [(r.profile_url, r.employer_domain, r.is_current) for r in found] == [
        ("https://www.linkedin.com/in/jane-doe", "synthetic-acme.example.com", True),
    ]
    assert all(r.raw_ref == "t/archive/x" and r.name == "Jane Doe" for r in found)
    # no brand known for the account: nothing can be confirmed, nothing is attached
    assert candidates_for("Jane Doe", links, account_domain="x.example", brand=None) == []
    assert candidates_for("Nobody Here", links, account_domain="x.example", brand="synthetic") == []


def test_a_page_of_an_erased_person_is_never_matched_again() -> None:
    links = [
        _link("https://www.linkedin.com/in/jane-doe", "Jane Doe - CIO - Synthetic Acme | LinkedIn"),
        _link("https://uk.linkedin.com/in/jane-doe/", "Jane Doe - CIO - Synthetic Acme | LinkedIn"),
    ]
    rule = load_people_config().profile_url
    erased = frozenset({"https://www.linkedin.com/in/jane-doe"})
    kept = candidates_for(
        "Jane Doe",
        links,
        account_domain="synthetic-acme.example.com",
        brand="synthetic",
        erased=erased,
        canonical=lambda url: canonical_profile_url(url, rule),
    )
    assert kept == []  # both addresses fold to the erased canonical one
    assert (
        len(
            candidates_for(
                "Jane Doe", links, account_domain="synthetic-acme.example.com", brand="synthetic"
            )
        )
        == 2
    )


class _Resolver:
    def __init__(self, answers: dict[str, list[RawPerson]], fail: bool = False) -> None:
        self.answers, self.fail = answers, fail
        self.asked: list[tuple[str, int]] = []

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        self.asked.append((name, attempt))
        if self.fail:
            raise SourceUnavailable("budget_cap")
        return PersonPage(records=tuple(self.answers.get(name, ())), call_ref=f"ref:{name}")


def by_id_of(out: Any) -> dict[str, RawPerson]:
    return {r.source_record_id: r for r in out.records}


def _raw(rid: str, name: str, url: str | None = None, **kw: Any) -> RawPerson:
    return RawPerson(source_record_id=rid, origin="title_search", name=name, profile_url=url, **kw)


@pytest.mark.asyncio
async def test_a_person_without_a_link_is_matched_to_the_archived_profile_page() -> None:
    cfg = load_people_config()
    records = [
        _raw("r1", "Omar Ali"),  # no link: exactly one page
        _raw("r2", "Sam Roe"),  # two pages: ambiguous, left alone
        _raw("r3", "Kim Park", "https://www.linkedin.com/in/kim-park"),  # linked already
        _raw("r4", None),  # nameless: nothing to ask
        _raw("r5", "Pat Lee"),  # only a namesake of another company: left unlinked
    ]
    resolver = _Resolver(
        {
            "Omar Ali": [
                _raw(
                    "a1",
                    "Omar Ali",
                    "https://www.linkedin.com/in/omar-ali",
                    employer_domain="acme.example",
                    is_current=True,
                )
            ],
            "Sam Roe": [
                _raw(
                    "b1",
                    "Sam Roe",
                    "https://www.linkedin.com/in/sam-roe",
                    employer_domain="acme.example",
                ),
                _raw(
                    "b2",
                    "Sam Roe",
                    "https://www.linkedin.com/in/sam-roe-1",
                    employer_domain="acme.example",
                ),
            ],
            # a namesake whose page does not name the account: never attached
            "Pat Lee": [_raw("c1", "Pat Lee", "https://www.linkedin.com/in/pat-lee")],
        }
    )
    out = await resolve_unlinked(records, resolver, cfg, lambda: False)
    assert (out.linked, out.ambiguous, out.exit_reason) == (1, 1, "converged")
    assert resolver.asked == [
        ("Omar Ali", 0),
        ("Sam Roe", 0),
        ("Pat Lee", 0),
    ]  # the linked and nameless are not asked
    assert by_id_of(out)["r5"].profile_url is None  # a name alone attaches nobody (F7)
    by_id = {r.source_record_id: r for r in out.records}
    assert by_id["r1"].profile_url == "https://www.linkedin.com/in/omar-ali"
    assert (by_id["r1"].employer_domain, by_id["r1"].is_current) == ("acme.example", True)
    assert by_id["r2"].profile_url is None and by_id["r3"].profile_url is not None
    assert [r.source_record_id for r in out.records] == ["r1", "r2", "r3", "r4", "r5"]  # order kept
    assert unlinked(out.records) == [by_id["r2"], by_id["r5"]]
    # a budget refusal stops asking; every record passes through unchanged
    stopped = await resolve_unlinked(records, _Resolver({}, fail=True), cfg, lambda: False)
    assert stopped.exit_reason == "budget" and stopped.linked == 0
    assert [r.source_record_id for r in stopped.records] == ["r1", "r2", "r3", "r4", "r5"]


@pytest.mark.asyncio
async def test_the_pipeline_links_a_source_record_that_came_without_a_profile_url() -> None:
    """The corpus returns Omar Ali once without a URL (r19) and the resolver knows his page: the
    record is linked before the merge, so it merges by URL (never by name alone) and the run
    counts it."""
    corpus = Corpus()
    kit = Kit(corpus)
    result = await kit.run()
    assert result.drops.get("unlinked_resolved") == 1
    r19 = next(p for p in result.people if p.source_record_id == "r19")
    assert r19.profile_url == "https://www.linkedin.com/in/omar-ali"
    omar = [p for p in result.people if p.source_record_id in {"r05", "r18", "r19"}]
    assert len({p.merged_into or p.person_key for p in omar}) == 1  # one person, by URL
    assert result.exit_reasons["unlinked"] == "converged"
