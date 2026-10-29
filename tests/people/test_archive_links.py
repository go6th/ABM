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


def test_candidates_carry_the_account_as_employer_only_when_the_title_names_the_brand() -> None:
    links = [
        _link("https://www.linkedin.com/in/jane-doe", "Jane Doe - CIO - Synthetic Acme | LinkedIn"),
        _link("https://www.linkedin.com/in/jane-doe-2", "Jane Doe - CFO - Other Co | LinkedIn"),
        _link("https://www.linkedin.com/in/raj-patel", "Raj Patel - Manager | LinkedIn"),
        _link("https://www.linkedin.com/in/no-title", None),
    ]
    found = candidates_for(
        "jane doe", links, account_domain="synthetic-acme.example.com", brand="synthetic"
    )
    assert [(r.profile_url, r.employer_domain, r.is_current) for r in found] == [
        ("https://www.linkedin.com/in/jane-doe", "synthetic-acme.example.com", True),
        ("https://www.linkedin.com/in/jane-doe-2", None, None),
    ]
    assert all(r.raw_ref == "t/archive/x" and r.name == "Jane Doe" for r in found)
    assert (
        candidates_for("Raj Patel", links, account_domain="x.example", brand=None)[0].title
        == "Manager"
    )
    assert candidates_for("Nobody Here", links, account_domain="x.example", brand=None) == []


class _Resolver:
    def __init__(self, answers: dict[str, list[RawPerson]], fail: bool = False) -> None:
        self.answers, self.fail = answers, fail
        self.asked: list[tuple[str, int]] = []

    async def resolve(self, name: str, attempt: int) -> PersonPage:
        self.asked.append((name, attempt))
        if self.fail:
            raise SourceUnavailable("budget_cap")
        return PersonPage(records=tuple(self.answers.get(name, ())), call_ref=f"ref:{name}")


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
                _raw("b1", "Sam Roe", "https://www.linkedin.com/in/sam-roe"),
                _raw("b2", "Sam Roe", "https://www.linkedin.com/in/sam-roe-1"),
            ],
        }
    )
    out = await resolve_unlinked(records, resolver, cfg, lambda: False)
    assert (out.linked, out.ambiguous, out.exit_reason) == (1, 1, "converged")
    assert resolver.asked == [
        ("Omar Ali", 0),
        ("Sam Roe", 0),
    ]  # the linked and nameless are not asked
    by_id = {r.source_record_id: r for r in out.records}
    assert by_id["r1"].profile_url == "https://www.linkedin.com/in/omar-ali"
    assert (by_id["r1"].employer_domain, by_id["r1"].is_current) == ("acme.example", True)
    assert by_id["r2"].profile_url is None and by_id["r3"].profile_url is not None
    assert [r.source_record_id for r in out.records] == ["r1", "r2", "r3", "r4"]  # order kept
    assert unlinked(out.records) == [by_id["r2"]]
    # a budget refusal stops asking; every record passes through unchanged
    stopped = await resolve_unlinked(records, _Resolver({}, fail=True), cfg, lambda: False)
    assert stopped.exit_reason == "budget" and stopped.linked == 0
    assert [r.source_record_id for r in stopped.records] == ["r1", "r2", "r3", "r4"]


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
