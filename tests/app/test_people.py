"""People merge, seniority and exclusions (roadmap s7 people pipeline, PRD s3.3)."""

from __future__ import annotations

import pytest

from app.people import (
    PersonRecord,
    infer_level,
    merge_people,
    normalize_name,
    normalize_profile_url,
)
from app.rules import RuleBundle


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Chief Information Officer", "C-level"),
        ("CIO", "C-level"),
        ("Executive Vice President, Operations", "EVP"),
        ("Senior Vice President of IT", "SVP"),
        ("SVP Engineering", "SVP"),
        ("Vice President, Enterprise Applications", "VP"),
        ("Head of Digital", "Head"),
        ("Senior Director, Platforms", "Director"),
        ("Senior Manager, Data", "Senior Manager"),
        ("Sr. Manager CRM", "Senior Manager"),
        ("Manager, Service Desk", None),
        ("Software Engineer", None),
    ],
)
def test_infer_level(title: str, level: str | None) -> None:
    assert infer_level(title) == level


def test_url_normalisation() -> None:
    assert normalize_profile_url("https://www.Profiles.example/in/Ab/") == "profiles.example/in/ab"
    assert normalize_profile_url("profiles.example/in/ab?x=1") == "profiles.example/in/ab"
    assert normalize_profile_url(None) is None
    assert normalize_profile_url("") is None


def test_name_normalisation() -> None:
    assert normalize_name("  José  O'Neil ") == "jose o neil"


def _p(name: str, title: str, url: str | None, source: str = "a") -> PersonRecord:
    return PersonRecord(name, title, url, source)


def test_merge_by_url_then_name(rules: RuleBundle) -> None:
    records = [
        _p("Ann Lee", "VP Sales", "https://p.example/in/ann", "a"),
        _p("Ann Lee", "Vice President Sales", "https://www.p.example/in/ann/", "b"),
        _p("Bo Chan", "Director CRM", "https://p.example/in/bo", "a"),
        _p("Bo  Chan", "Director, CRM", None, "b"),
    ]
    result = merge_people(records, rules.people_scope)
    assert len(result.people) == 2
    assert all(p.sources == {"a", "b"} for p in result.people)


def test_name_match_fills_a_missing_url(rules: RuleBundle) -> None:
    records = [
        _p("Cy Dee", "Director IT", None, "a"),
        _p("Cy Dee", "Director IT", "p.example/cy", "b"),
    ]
    (person,) = merge_people(records, rules.people_scope).people
    assert person.profile_url == "p.example/cy"


def test_same_name_different_profiles_stay_separate(rules: RuleBundle) -> None:
    records = [
        _p("Sam Roe", "Director Sales", "https://p.example/in/sam-1"),
        _p("Sam Roe", "Director Finance", "https://p.example/in/sam-2"),
    ]
    assert len(merge_people(records, rules.people_scope).people) == 2


def test_exclusions_and_scope(rules: RuleBundle) -> None:
    records = [
        _p("A", "Director of IT Security", "u1"),
        _p("B", "VP, IT Compliance", "u2"),
        _p("C", "Head of IT Audit", "u3"),
        _p("D", "Manager, Payroll", "u4"),
        _p("E", "Director, Applications", "u5"),
    ]
    result = merge_people(records, rules.people_scope)
    assert [p.name for p in result.people] == ["E"]
    assert [r.name for r in result.excluded] == ["A", "B", "C"]
    assert [r.name for r in result.out_of_scope] == ["D"]
    assert result.keys() == {"u5"}
