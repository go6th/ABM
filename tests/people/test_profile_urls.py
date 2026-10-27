"""Profile links in every public form fold to one canonical link (2026-10-27 audit): regional
hosts, the mobile site, locale and sub-page suffixes, query strings, percent-encoded names and
links written without a scheme. Pages on the profile host that are not a person's own (a
company, a post, a job, the directory search) identify nobody, so two people pointing at one
company page are never merged by it."""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from agents.production.pipelines import people_counters
from people.config import ProfileUrlRule, load_people_config
from people.merge import MergeItem, stable_plan
from people.models import Person, RawPerson
from people.names import looks_like_person
from people.pipeline import PeoplePipeline, RunInputs
from people.urls import canonical_profile_url
from tests.people.conftest import frozen_people_config

RULE = ProfileUrlRule("www.linkedin.com", "linkedin.com", True)
JANE = "https://www.linkedin.com/in/jane-doe"


@pytest.mark.parametrize(
    "variant",
    [
        "https://www.linkedin.com/in/jane-doe",
        "https://uk.linkedin.com/in/jane-doe",
        "https://de.linkedin.com/in/jane-doe/",
        "https://m.linkedin.com/in/jane-doe",
        "http://linkedin.com/in/Jane-Doe?originalSubdomain=uk",
        "https://www.linkedin.com/in/jane-doe/en",
        "https://www.linkedin.com/in/jane-doe/details/experience/",
        "https://www.linkedin.com/mwlite/in/jane-doe",
        "www.linkedin.com/in/jane-doe",
        "linkedin.com/in/jane-doe/",
        "//uk.linkedin.com/in/jane-doe",
        "  https://www.linkedin.com/in/jane-doe#experience  ",
    ],
)
def test_profile_url_regional_and_suffixed_forms_fold_to_one(variant: str) -> None:
    assert canonical_profile_url(variant, RULE) == JANE


def test_profile_url_encoded_and_accented_names_are_one_person() -> None:
    encoded = canonical_profile_url("https://fr.linkedin.com/in/j%C3%A9r%C3%B4me-dupont/", RULE)
    literal = canonical_profile_url("https://www.linkedin.com/in/Jérôme-Dupont", RULE)
    lower_hex = canonical_profile_url("https://www.linkedin.com/in/j%c3%a9r%c3%b4me-dupont", RULE)
    assert encoded == literal == lower_hex == "https://www.linkedin.com/in/j%C3%A9r%C3%B4me-dupont"


def test_profile_url_old_public_form_is_kept() -> None:
    assert canonical_profile_url("https://uk.linkedin.com/pub/jane-doe/1a/2b/3c/", RULE) == (
        "https://www.linkedin.com/pub/jane-doe/1a/2b/3c"
    )


@pytest.mark.parametrize(
    "page",
    [
        "https://www.linkedin.com/company/acme",
        "https://www.linkedin.com/company/acme/people/",
        "https://www.linkedin.com/posts/jane-doe-1",
        "https://www.linkedin.com/jobs/view/123",
        "https://www.linkedin.com/pub/dir/jane/doe",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/",
        "linkedin.com",
        "jane-doe",
    ],
)
def test_profile_url_non_person_pages_identify_nobody(page: str) -> None:
    assert canonical_profile_url(page, RULE) is None


def test_two_people_on_one_company_page_are_not_merged_by_it() -> None:
    def item(key: str, name: str) -> MergeItem:
        return MergeItem(
            key=key,
            url=canonical_profile_url("https://www.linkedin.com/company/acme", RULE),
            name=name,
            employer_domain=None,
            lanes=frozenset(),
            title_tokens=frozenset(),
        )

    plan = stable_plan([item("a", "jane doe"), item("b", "omar ali")], 0.5, 10)
    assert plan.merged_into("a") is None and plan.merged_into("b") is None


def test_the_configured_rule_reads_the_person_paths() -> None:
    rule = load_people_config().profile_url
    assert rule.person_paths == ("in", "pub")
    assert canonical_profile_url("https://ca.linkedin.com/in/jane-doe/fr", rule) == JANE


@pytest.mark.parametrize(
    "name",
    [
        "José Müller",
        "Łukasz Nowak",
        "Zoë Brontë",
        "王小明",
        "Pieter van der Berg",
        "Jane Doe (she/her)",
    ],
)
def test_accented_and_non_latin_names_are_people(name: str) -> None:
    assert looks_like_person(name)


@pytest.mark.parametrize(
    "name", ["Technology Product Management", "van Berg", "Ann", "Sales y Marketing"]
)
def test_role_words_and_fragments_are_not_people(name: str) -> None:
    assert not looks_like_person(name)


# ---- through the pipeline's rows ---------------------------------------------------------------


def _rows(records: list[RawPerson]) -> dict[str, Person]:
    pipeline = PeoplePipeline(
        frozen_people_config(),
        *([cast(Any, None)] * 8),  # build_rows reads no port
        canary="c",
        hostile_markers=(),
        is_hostile=lambda _t: False,
    )
    inputs = RunInputs(
        account_domain="acme.example", as_of=date(2026, 10, 27), lanes_off=frozenset()
    )
    rows, _ = pipeline.build_rows(records, inputs)
    return {p.source_record_id: p for p in rows}


def _record(
    rid: str, name: str, url: str | None, title: str = "VP, Salesforce Platform"
) -> RawPerson:
    return RawPerson(
        source_record_id=rid,
        origin="association",
        name=name,
        title=title,
        profile_url=url,
        search_domain="acme.example",
    )


def test_regional_links_and_accented_names_reach_the_list_with_their_link() -> None:
    rows = _rows(
        [
            _record("a", "José Müller", "https://es.linkedin.com/in/jose-muller/"),
            _record("b", "José Müller", "linkedin.com/in/jose-muller?trk=x"),
            _record("c", "Łukasz Nowak", "https://pl.linkedin.com/in/lukasz-nowak/en"),
        ]
    )
    jose = rows["a"] if rows["a"].merged_into is None else rows["b"]
    assert {rows["a"].merged_into, rows["b"].merged_into} == {None, jose.person_key}
    assert jose.status == "in_scope"
    assert jose.profile_url == "https://www.linkedin.com/in/jose-muller"
    assert rows["c"].status == "in_scope"
    assert rows["c"].profile_url == "https://www.linkedin.com/in/lukasz-nowak"


def test_a_company_page_link_is_no_profile_link_and_is_counted() -> None:
    rows = _rows(
        [
            _record("a", "Jane Doe", "https://www.linkedin.com/company/acme"),
            _record("b", "Omar Ali", "https://www.linkedin.com/company/acme"),
            _record("c", "Vice President of Technology Product Management", None),
        ]
    )
    assert rows["a"].merged_into is None and rows["b"].merged_into is None
    assert rows["a"].status == rows["b"].status == "unresolved"
    assert rows["a"].profile_url is None
    assert rows["a"].profile_url_raw == "https://www.linkedin.com/company/acme"  # kept as found
    assert rows["c"].classification.reason == "not_a_person_name"
    counters = people_counters(list(rows.values()), 0, 0)
    assert counters["people_no_profile_link"] == 2
    assert counters["people_not_a_person"] == 1
    assert counters["people_in_scope"] == 0
